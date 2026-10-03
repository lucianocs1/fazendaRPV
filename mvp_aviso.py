"""
MVP: transforma a lista de clientes afetados por um desligamento em uma lista
de comunidades afetadas e gera o aviso de rádio.

Pipeline:
  1. completa coordenadas ausentes com a do transformador;
  2. extrai o nome da comunidade do texto do endereço (abreviações, erros);
  3. agrupa os clientes por proximidade (DBSCAN);
  4. nomeia cada grupo cruzando base oficial + endereços, com nível de confiança;
  5. monta o aviso para a chave/alimentador desligado e compara com o atual.

Uso:
    python gerar_cenario.py
    python mvp_aviso.py CH-101        # ou AL-01, CH-301...
"""

import math
import re
import sys
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

DADOS = Path(__file__).parent / "dados"
SAIDAS = Path(__file__).parent / "saidas"

DATA, HORARIO = "15/10", "das 8h às 14h"
PALAVRAS_POR_MINUTO = 150
LIMIAR_COMUNIDADE_INTEIRA = 0.70   # acima disso cita "comunidade X"; abaixo, "parte da comunidade X"
DBSCAN_EPS_M = 350
DBSCAN_MIN_AMOSTRAS = 5
DIST_MAX_OFICIAL_M = 2000          # raio para casar um grupo com a localidade oficial
SIMILARIDADE_MIN = 0.80            # para unificar grafias ("Sta Rita" ~ "Santa Rita")

ABREVIACOES = {
    "sta": "santa", "sto": "santo", "s": "sao", "com": "comunidade", "pov": "povoado",
    "cor": "corrego", "corr": "corrego", "bca": "branca", "cap": "capao", "b": "boa",
}
PREFIXOS = r"^(comunidade|bairro rural|bairro|povoado|localidade)\s+((do|da|de|dos|das)\s+)?"
GENERICOS = ("zona rural", "area rural", "sn zona rural", "estrada", "rodovia", "km")


# --------------------------------------------------------------------------- util
def normalizar(texto):
    """minúsculas, sem acento, sem pontuação, abreviações expandidas."""
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return " ".join(ABREVIACOES.get(p, p) for p in t.split())


def similaridade(a, b):
    return SequenceMatcher(None, a, b).ratio()


def para_metros(lat, lon, lat0):
    y = lat * 111_320.0
    x = lon * 111_320.0 * math.cos(math.radians(lat0))
    return np.column_stack([x, y])


def contar_palavras(texto):
    return len(re.findall(r"\w+", texto))


def duracao(palavras):
    seg = round(palavras / PALAVRAS_POR_MINUTO * 60)
    return f"{seg // 60} min {seg % 60:02d} s" if seg >= 60 else f"{seg} s"


def juntar(itens):
    itens = list(itens)
    return itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1]


# ----------------------------------------------------------------- passo 1
def completar_coordenadas(cli, trafos):
    cli = cli.merge(trafos, on="trafo_id", suffixes=("", "_trafo"))
    cli["coord_herdada"] = cli["lat"].isna()
    cli["lat"] = cli["lat"].fillna(cli["lat_trafo"])
    cli["lon"] = cli["lon"].fillna(cli["lon_trafo"])
    return cli


# ----------------------------------------------------------------- passo 2
def extrair_nomes(cli, oficiais):
    """Devolve, para cada cliente, o nome canônico da comunidade citada no endereço (ou None)."""
    # parte depois do nome da propriedade ("Sítio X, <localidade>")
    brutos = cli["endereco"].str.split(",", n=1).str[1].str.strip().fillna("")

    def candidato(texto):
        n = re.sub(PREFIXOS, "", normalizar(texto)).strip()
        if not n or any(n.startswith(g) for g in GENERICOS):
            return None
        return n

    def limpar_exibicao(texto):
        return re.sub(PREFIXOS, "", texto, flags=re.IGNORECASE).strip()

    cands = brutos.map(candidato)

    # vocabulário: nomes oficiais + nomes frequentes nos endereços que não batem com nenhum oficial
    canon = {normalizar(n): n for n in oficiais["nome"]}
    for c, freq in Counter(cands.dropna()).most_common():
        if freq < 3 or max((similaridade(c, k) for k in canon), default=0) >= SIMILARIDADE_MIN:
            continue
        exibicao = Counter(limpar_exibicao(b) for b, x in zip(brutos, cands) if x == c).most_common(1)[0][0]
        canon[c] = exibicao

    def casar(c):
        if not isinstance(c, str):
            return None
        melhor = max(canon, key=lambda k: similaridade(c, k))
        return canon[melhor] if similaridade(c, melhor) >= SIMILARIDADE_MIN else None

    return cands.map(casar)


# ----------------------------------------------------------------- passo 3 e 4
def agrupar_e_nomear(cli, oficiais):
    lat0 = cli["lat"].mean()
    xy = para_metros(cli["lat"].values, cli["lon"].values, lat0)
    cli["grupo"] = DBSCAN(eps=DBSCAN_EPS_M, min_samples=DBSCAN_MIN_AMOSTRAS).fit_predict(xy)
    cli["x"], cli["y"] = xy[:, 0], xy[:, 1]
    of_xy = para_metros(oficiais["lat"].values, oficiais["lon"].values, lat0)

    grupos = []
    for g, membros in cli[cli["grupo"] >= 0].groupby("grupo"):
        cx, cy = membros["x"].mean(), membros["y"].mean()
        votos = Counter(membros["nome_extraido"].dropna())
        nome_end, n_votos = votos.most_common(1)[0] if votos else (None, 0)
        participacao = n_votos / max(sum(votos.values()), 1)

        dists = np.hypot(of_xy[:, 0] - cx, of_xy[:, 1] - cy)
        i = int(np.argmin(dists))
        nome_of = oficiais["nome"].iloc[i] if dists[i] <= DIST_MAX_OFICIAL_M else None

        if nome_end and n_votos >= 3 and participacao >= 0.6:
            nome = nome_end
            if nome_of == nome_end:
                conf, fonte = "alta", "base oficial + endereços"
            else:
                conf, fonte = "média", "endereços" + (f" (oficial mais próxima: {nome_of})" if nome_of else "")
        elif nome_of and dists[i] <= DIST_MAX_OFICIAL_M / 2:
            nome, conf, fonte = nome_of, "média", "localidade oficial mais próxima"
        else:
            nome, conf, fonte = f"Grupo {g} (pendente)", "baixa", "sem nome: revisão humana"

        grupos.append(dict(grupo=g, nome=nome, confianca=conf, fonte=fonte, clientes=len(membros),
                           dist_oficial_m=round(float(dists[i])), cx=cx, cy=cy))
    grupos = pd.DataFrame(grupos)
    cli = cli.merge(grupos[["grupo", "nome", "confianca"]], on="grupo", how="left")
    cli["comunidade_prevista"] = np.where(
        cli["confianca"].isin(["alta", "média"]), cli["nome"], "ISOLADO")
    return cli, grupos


# ----------------------------------------------------------------- passo 5
def lado(dx, dy):
    if abs(dx) >= abs(dy):
        return "lado leste" if dx > 0 else "lado oeste"
    return "lado norte" if dy > 0 else "lado sul"


def resumir_isolados(enderecos, max_nomes_por_estrada=2):
    """Agrupa propriedades isoladas por estrada: cita o trecho de km em vez de cada sítio."""
    por_estrada, nomes = {}, []
    for e in enderecos:
        m = re.search(r"^(.*?),\s*((?:Estrada|Rodovia)[^,]*),\s*km\s*(\d+)", e)
        if m:
            por_estrada.setdefault(m.group(2), []).append((int(m.group(3)), m.group(1)))
        else:
            prop, _, local = e.partition(",")
            nomes.append(f"{prop} ({local.strip()})" if local else prop)
    trechos = []
    for estrada, itens in sorted(por_estrada.items()):
        kms = sorted(k for k, _ in itens)
        if len(itens) <= max_nomes_por_estrada:
            nomes += [f"{nome} ({estrada}, km {k})" for k, nome in itens]
        elif kms[0] == kms[-1]:
            trechos.append(f"nas propriedades da {estrada} na altura do km {kms[0]}")
        else:
            trechos.append(f"nas propriedades da {estrada} entre os km {kms[0]} e {kms[-1]}")
    if nomes:
        trechos.append(("na propriedade " if len(nomes) == 1 else "nas propriedades ") + juntar(nomes))
    return trechos


def montar_avisos(cli, grupos, equipamento):
    afetados = cli[(cli["chave_id"] == equipamento) | (cli["alimentador"] == equipamento)]
    if afetados.empty:
        raise SystemExit(f"Nenhum cliente encontrado para '{equipamento}'.")

    abertura = (f"Comunicado da distribuidora: no dia {DATA}, {HORARIO}, haverá desligamento "
                f"programado de energia para manutenção da rede")
    fechamento = "Agradecemos a compreensão."

    atual = (f"{abertura}, atingindo as seguintes propriedades: "
             + "; ".join(afetados["endereco"]) + f". {fechamento}")

    inteiras, parciais, individuais = [], [], []
    for g, membros in afetados.groupby("grupo"):
        info = grupos.set_index("grupo").loc[g] if g >= 0 else None
        if info is None or info["confianca"] == "baixa":
            individuais += list(membros["endereco"])
            continue
        frac = len(membros) / info["clientes"]
        if frac >= LIMIAR_COMUNIDADE_INTEIRA:
            inteiras.append(info["nome"])
        else:
            parciais.append(f"{info['nome']} ({lado(membros['x'].mean() - info['cx'], membros['y'].mean() - info['cy'])})")

    trechos = []
    if inteiras:
        trechos.append(("na comunidade " if len(inteiras) == 1 else "nas comunidades ") + juntar(inteiras))
    if parciais:
        trechos.append(("em parte da comunidade " if len(parciais) == 1 else "em parte das comunidades ")
                       + juntar(parciais))
    if individuais:
        trechos += resumir_isolados(individuais)
    novo = f"{abertura}, {juntar(trechos)}. {fechamento}"
    return afetados, atual, novo


def salvar_mapa(cli, grupos, oficiais, afetados, equipamento):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    SAIDAS.mkdir(exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 9))
    ax.scatter(cli["lon"], cli["lat"], s=10, c="#cccccc", label="clientes não afetados")
    cores = plt.get_cmap("tab10")
    for i, (g, m) in enumerate(afetados.groupby("grupo")):
        rot = "isolados" if g < 0 else grupos.set_index("grupo").loc[g, "nome"]
        ax.scatter(m["lon"], m["lat"], s=22, color="black" if g < 0 else cores(i % 10),
                   marker="x" if g < 0 else "o", label=f"afetados: {rot}")
    ax.scatter(oficiais["lon"], oficiais["lat"], marker="^", s=90, c="none", edgecolors="red",
               label="localidades oficiais")
    for _, o in oficiais.iterrows():
        ax.annotate(o["nome"], (o["lon"], o["lat"]), xytext=(4, 4), textcoords="offset points",
                    fontsize=8, color="red")
    for _, gr in grupos.iterrows():
        m = cli[cli["grupo"] == gr["grupo"]]
        ax.annotate(f"{gr['nome']}\n[{gr['confianca']}]", (m["lon"].mean(), m["lat"].mean()),
                    xytext=(0, -22), textcoords="offset points", ha="center", fontsize=8)
    ax.set_title(f"Desligamento {equipamento}: {len(afetados)} clientes afetados")
    ax.set_xlabel("longitude"); ax.set_ylabel("latitude")
    ax.legend(fontsize=7, loc="best")
    caminho = SAIDAS / f"mapa_{equipamento.lower().replace('-', '_')}.png"
    fig.tight_layout(); fig.savefig(caminho, dpi=120); plt.close(fig)
    return caminho


# ----------------------------------------------------------------- main
def main():
    equipamento = sys.argv[1].upper() if len(sys.argv) > 1 else "CH-101"
    cli = pd.read_csv(DADOS / "clientes.csv")
    trafos = pd.read_csv(DADOS / "trafos.csv", keep_default_na=False)
    trafos[["lat", "lon"]] = trafos[["lat", "lon"]].astype(float)
    oficiais = pd.read_csv(DADOS / "localidades_oficiais.csv")

    cli = completar_coordenadas(cli, trafos)
    cli["nome_extraido"] = extrair_nomes(cli, oficiais)
    cli, grupos = agrupar_e_nomear(cli, oficiais)

    print(f"1. Coordenadas herdadas do transformador: {cli['coord_herdada'].sum()} UCs")
    print(f"2. Nome de comunidade extraído do endereço: {cli['nome_extraido'].notna().sum()} de {len(cli)} UCs")
    print(f"3/4. Grupos encontrados (DBSCAN eps={DBSCAN_EPS_M} m):")
    print(grupos[["grupo", "nome", "clientes", "confianca", "fonte"]].to_string(index=False))
    acuracia = (cli["comunidade_prevista"] == cli["comunidade_real"]).mean()
    print(f"   Acurácia cliente→comunidade (vs. verdade de campo): {acuracia:.1%}")

    afetados, atual, novo = montar_avisos(cli, grupos, equipamento)
    pa, pn = contar_palavras(atual), contar_palavras(novo)
    print(f"\n5. Desligamento {equipamento}: {len(afetados)} clientes afetados")
    print(f"\n--- Aviso ATUAL ({pa} palavras, ~{duracao(pa)} de rádio) ---")
    print(atual[:400] + (" [...]" if len(atual) > 400 else ""))
    print(f"\n--- Aviso NOVO ({pn} palavras, ~{duracao(pn)} de rádio) ---")
    print(novo)
    print(f"\nRedução: {1 - pn / pa:.0%} no tempo de veiculação")

    SAIDAS.mkdir(exist_ok=True)
    tag = equipamento.lower().replace("-", "_")
    (SAIDAS / f"aviso_{tag}.txt").write_text(f"ATUAL:\n{atual}\n\nNOVO:\n{novo}\n", encoding="utf-8")
    cli.drop(columns=["x", "y"]).to_csv(SAIDAS / "clientes_com_comunidade.csv", index=False)
    print(f"Mapa salvo em {salvar_mapa(cli, grupos, oficiais, afetados, equipamento)}")


if __name__ == "__main__":
    main()
