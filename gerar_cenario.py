"""
Gera um cenário sintético para o MVP "Fazenda por fazenda, ou uma comunidade só?".

Município fictício com 8 comunidades rurais, 30 sítios isolados e uma rede
elétrica simplificada (3 alimentadores, 5 chaves de ramal, transformadores).

Problemas do mundo real inseridos de propósito:
  - endereços "sujos" (abreviações, erros de digitação, ~35% sem nome da comunidade);
  - ~5% das UCs sem coordenada (devem herdar a do transformador);
  - base oficial imperfeita: falta a Cachoeirinha, o Barreiro está deslocado
    1,2 km e existe uma "Fazenda Velha" sem nenhum cliente;
  - Santa Rita é atendida por dois ramais (CH-101 e CH-102).

A coluna `comunidade_real` é a verdade de campo, usada para medir a acurácia.

Saída: pasta dados/ com clientes.csv, trafos.csv, chaves.csv e
localidades_oficiais.csv.
"""

import math
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
PASTA = Path(__file__).parent / "dados"

# Origem fictícia para converter metros em lat/lon (coordenadas não reais).
LAT0, LON0 = -18.50, -46.50
M_POR_GRAU_LAT = 111_320.0
M_POR_GRAU_LON = 111_320.0 * math.cos(math.radians(LAT0))

# Estradas rurais (polilinhas em km, a partir da sede em (0, 0)).
ESTRADAS = {
    "Estrada do Cascalho": [(0, 0), (2, 4), (4, 6), (8, 9), (12, 11)],
    "Estrada da Serra": [(0, 0), (5, 2), (12, 4), (15, 8)],
    "Estrada Velha": [(0, 0), (3, -2), (6, -3), (11, -6)],
    "Rodovia Municipal 040": [(5, 2), (10, 0), (16, -2)],
}

# nome, centro (km), raio (m), nº de clientes, ramal (chave) e variações de grafia
COMUNIDADES = [
    ("Santa Rita", (4.0, 6.0), 450, 34, "SPLIT",
     ["Comunidade Santa Rita", "Com. Sta. Rita", "Sta Rita", "Bairro Rural Santa Rita", "Santa Rita", "Sta. Rita"]),
    ("Cachoeirinha", (1.5, 8.0), 350, 20, "CH-101",
     ["Comunidade Cachoeirinha", "Cachoeirinha", "Cachoerinha", "Povoado da Cachoeirinha"]),
    ("Água Limpa", (8.0, 9.5), 400, 26, "CH-102",
     ["Comunidade Água Limpa", "Agua Limpa", "Bairro Água Limpa", "Com. Agua Limpa", "Agua Lipma"]),
    ("Barreiro", (12.0, 4.5), 400, 24, "CH-201",
     ["Comunidade do Barreiro", "Barreiro", "Povoado Barreiro", "Bareiro"]),
    ("Córrego Fundo", (15.0, 8.0), 350, 22, "CH-201",
     ["Comunidade Córrego Fundo", "Cór. Fundo", "Corrego Fundo", "Corr. Fundo"]),
    ("Boa Vista", (6.0, -3.5), 400, 25, "CH-301",
     ["Comunidade Boa Vista", "Boa Vista", "B. Vista", "Bairro Rural Boa Vista"]),
    ("Pedra Branca", (11.0, -6.5), 350, 20, "CH-302",
     ["Comunidade Pedra Branca", "Pedra Branca", "Pedra Bca", "Povoado Pedra Branca"]),
    ("Capão Seco", (16.0, -2.5), 300, 22, None,  # tronco do AL-02, sem chave de ramal
     ["Comunidade Capão Seco", "Capao Seco", "Capão Sêco", "Cap. Seco"]),
]

CHAVES = [
    ("CH-101", "AL-01", "Ramal Cachoeirinha / Santa Rita (oeste)"),
    ("CH-102", "AL-01", "Ramal Santa Rita (leste) / Água Limpa"),
    ("CH-201", "AL-02", "Ramal Barreiro / Córrego Fundo"),
    ("CH-301", "AL-03", "Ramal Boa Vista"),
    ("CH-302", "AL-03", "Ramal Pedra Branca"),
]
ALIMENTADOR_DA_CHAVE = {c: a for c, a, _ in CHAVES}

TIPOS_PROPRIEDADE = ["Sítio"] * 6 + ["Fazenda"] * 2 + ["Chácara"] * 2
NOMES_PROPRIEDADE = [
    "São José", "Boa Esperança", "Santa Clara", "Recanto Verde", "Bela Vista",
    "Três Irmãos", "Santo Antônio", "Paraíso", "Primavera", "Pau d'Alho",
    "Nossa Senhora Aparecida", "São Pedro", "Sossego", "Alvorada", "Bom Jesus",
    "Água Branca", "Ipê Amarelo", "Morro Alto", "Lagoa Seca", "Monte Alegre",
    "São Sebastião", "Vista Alegre", "Cachoeira", "Pinheirinho", "Palmeiras",
    "Santa Luzia", "Retiro", "Barra Mansa", "Boa Sorte", "Jatobá",
    "Mangueiras", "Santa Helena", "Estrela", "Capim Branco", "Taquaral",
    "Boa Vista",  # coincide com o nome de uma comunidade, de propósito
]
GENERICOS = ["Zona Rural", "Zona Rural", "Área Rural", "s/n, Zona Rural"]


def km_para_latlon(x_km, y_km):
    return LAT0 + (y_km * 1000) / M_POR_GRAU_LAT, LON0 + (x_km * 1000) / M_POR_GRAU_LON


def ponto_na_estrada(rng, nome):
    """Sorteia um ponto ao longo da estrada; devolve (x, y, km)."""
    pts = np.array(ESTRADAS[nome], dtype=float)
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    alvo = rng.uniform(0.5, seg.sum())
    acum = 0.0
    for i, s in enumerate(seg):
        if acum + s >= alvo:
            t = (alvo - acum) / s
            x, y = pts[i] + t * (pts[i + 1] - pts[i])
            return x, y, alvo
        acum += s
    return *pts[-1], seg.sum()


def estrada_mais_proxima(x, y):
    """Estrada e km aproximado mais próximos de (x, y) em km."""
    melhor = (None, math.inf, 0.0)
    for nome, pts in ESTRADAS.items():
        pts = np.array(pts, dtype=float)
        acum = 0.0
        for a, b in zip(pts[:-1], pts[1:]):
            ab = b - a
            t = np.clip(np.dot([x, y] - a, ab) / np.dot(ab, ab), 0, 1)
            d = np.linalg.norm(a + t * ab - [x, y])
            if d < melhor[1]:
                melhor = (nome, d, acum + t * np.linalg.norm(ab))
            acum += np.linalg.norm(ab)
    return melhor[0], melhor[2]


def nome_propriedade(rng):
    return f"{rng.choice(TIPOS_PROPRIEDADE)} {rng.choice(NOMES_PROPRIEDADE)}"


def ponto_no_disco(rng, cx, cy, raio_m):
    r = raio_m * math.sqrt(rng.uniform()) / 1000
    ang = rng.uniform(0, 2 * math.pi)
    return cx + r * math.cos(ang), cy + r * math.sin(ang)


def main():
    rng = np.random.default_rng(SEED)
    PASTA.mkdir(exist_ok=True)

    trafos, clientes = [], []
    n_trafo, n_uc = 0, 0

    def novo_trafo(x, y, chave, alimentador):
        nonlocal n_trafo
        n_trafo += 1
        tid = f"TR-{n_trafo:03d}"
        lat, lon = km_para_latlon(x, y)
        trafos.append(dict(trafo_id=tid, lat=lat, lon=lon, chave_id=chave or "", alimentador=alimentador))
        return tid

    def novo_cliente(x, y, trafo_id, endereco, propriedade, comunidade_real):
        nonlocal n_uc
        n_uc += 1
        lat, lon = km_para_latlon(x, y)
        if rng.uniform() < 0.05:  # UC sem coordenada
            lat, lon = np.nan, np.nan
        clientes.append(dict(uc_id=f"UC-{n_uc:05d}", propriedade=propriedade, endereco=endereco,
                             lat=lat, lon=lon, trafo_id=trafo_id, comunidade_real=comunidade_real))

    # --- Comunidades: ~3 clientes por transformador -------------------------
    for nome, (cx, cy), raio, n, chave, variacoes in COMUNIDADES:
        n_tr = math.ceil(n / 3)
        posicoes_tr = [ponto_no_disco(rng, cx, cy, raio * 0.8) for _ in range(n_tr)]
        ids_tr = []
        for tx, ty in posicoes_tr:
            if chave == "SPLIT":  # Santa Rita: lado oeste no CH-101, leste no CH-102
                ch = "CH-101" if tx < cx - 0.08 else "CH-102"
            else:
                ch = chave
            alim = ALIMENTADOR_DA_CHAVE.get(ch, "AL-02")
            ids_tr.append(novo_trafo(tx, ty, ch, alim))

        for i in range(n):
            k = i % n_tr
            tx, ty = posicoes_tr[k]
            x, y = ponto_no_disco(rng, tx, ty, 150)
            prop = nome_propriedade(rng)
            if rng.uniform() < 0.65:
                local = rng.choice(variacoes)
            elif rng.uniform() < 0.5:
                local = rng.choice(GENERICOS)
            else:
                est, km = estrada_mais_proxima(x, y)
                local = f"{est}, km {km:.0f}"
            novo_cliente(x, y, ids_tr[k], f"{prop}, {local}", prop, nome)

    # --- Sítios isolados: 1 transformador cada, ao longo das estradas -------
    centros = [(c[1], c[4]) for c in COMUNIDADES]
    criados = 0
    while criados < 30:
        est = rng.choice(list(ESTRADAS))
        x, y, km = ponto_na_estrada(rng, est)
        desl = rng.uniform(0.1, 0.6) * rng.choice([-1, 1])
        x, y = x + desl * rng.uniform(-1, 1), y + desl
        # mantém distância das comunidades para não "virar" parte delas
        dists = [math.dist((x, y), c) for c, _ in centros]
        if min(dists) < 1.2:
            continue
        chave = centros[int(np.argmin(dists))][1]
        if chave == "SPLIT":
            chave = "CH-101" if x < centros[0][0][0] else "CH-102"
        alim = ALIMENTADOR_DA_CHAVE.get(chave, "AL-02")
        tid = novo_trafo(x, y, chave, alim)
        prop = nome_propriedade(rng)
        novo_cliente(x, y, tid, f"{prop}, {est}, km {km:.0f}", prop, "ISOLADO")
        criados += 1

    # --- Base "oficial" de localidades (simula IBGE/OSM), com defeitos ------
    locs = []
    for nome, (cx, cy), *_ in COMUNIDADES:
        if nome == "Cachoeirinha":
            continue  # ausente na base oficial
        if nome == "Barreiro":
            cx += 1.2  # ponto deslocado 1,2 km
        lat, lon = km_para_latlon(cx, cy)
        locs.append(dict(nome=nome, tipo="aglomerado rural", lat=lat, lon=lon))
    lat, lon = km_para_latlon(18.0, 12.0)
    locs.append(dict(nome="Fazenda Velha", tipo="localidade", lat=lat, lon=lon))  # sem clientes

    pd.DataFrame(clientes).to_csv(PASTA / "clientes.csv", index=False)
    pd.DataFrame(trafos).to_csv(PASTA / "trafos.csv", index=False)
    pd.DataFrame(CHAVES, columns=["chave_id", "alimentador", "descricao"]).to_csv(PASTA / "chaves.csv", index=False)
    pd.DataFrame(locs).to_csv(PASTA / "localidades_oficiais.csv", index=False)

    df = pd.DataFrame(clientes)
    print(f"Cenário gerado em {PASTA}/")
    print(f"  clientes: {len(df)}  (sem coordenada: {df['lat'].isna().sum()})")
    print(f"  transformadores: {len(trafos)}  chaves: {len(CHAVES)}  alimentadores: 3")
    print(f"  localidades oficiais: {len(locs)}")
    print(df["comunidade_real"].value_counts().to_string())


if __name__ == "__main__":
    main()
