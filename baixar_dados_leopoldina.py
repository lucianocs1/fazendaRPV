"""
Baixa os dados públicos de Leopoldina (MG) usados no MVP com endereços reais.

Fontes (nenhuma tem dado pessoal):
  - IBGE, malhas territoriais: limite do município e dos distritos (GeoJSON);
  - IBGE, lista de distritos (nomes);
  - IBGE, CNEFE do Censo 2022: endereços com coordenada, inclusive rurais,
    com o nome da localidade (ex.: "CORREGO DO ...");
  - OpenStreetMap (Overpass): povoados/localidades com nome, estradas e
    elementos de rede elétrica que estiverem mapeados.

Tudo vai para dados_leopoldina/brutos/. Cada fonte é independente: se uma
falhar, as outras continuam e o resumo no final mostra o que faltou.

Uso:
    python baixar_dados_leopoldina.py

Em rede corporativa com proxy, defina antes HTTPS_PROXY (e, se houver
inspeção de SSL, SSL_CERT_FILE apontando para o certificado da empresa).
"""

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

COD_IBGE = "3138401"  # Leopoldina - MG
PASTA = Path(__file__).parent / "dados_leopoldina" / "brutos"

MALHAS = "https://servicodados.ibge.gov.br/api/v3/malhas/municipios/" + COD_IBGE
LOCALIDADES = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios/" + COD_IBGE
CNEFE_DIR = ("https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/"
             "Censo_Demografico_2022/Arquivos_CNEFE/CSV/Municipio/31_MG/")
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]

CONSULTA_OSM = f"""
[out:json][timeout:180];
area["IBGE:GEOCODIGO"="{COD_IBGE}"]["boundary"="administrative"]->.mun;
(
  node["place"~"^(village|hamlet|locality|isolated_dwelling|neighbourhood|suburb|farm|quarter)$"](area.mun);
  way["place"](area.mun);
  way["highway"~"^(trunk|primary|secondary|tertiary|unclassified|track|road|residential)$"](area.mun);
  node["power"~"^(substation|transformer|switch)$"](area.mun);
  way["power"~"^(substation|line|minor_line)$"](area.mun);
);
out geom tags;
"""

CABECALHO = {"User-Agent": "fazendaRPV-mvp/1.0 (pesquisa academica)"}


def baixar(url, destino=None, dados=None, timeout=300):
    req = urllib.request.Request(url, data=dados, headers=CABECALHO)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        conteudo = resp.read()
    if destino:
        destino.write_bytes(conteudo)
    return conteudo


def passo(nome, func, resultados):
    print(f"- {nome} ... ", end="", flush=True)
    try:
        info = func()
        print(f"ok {info or ''}")
        resultados[nome] = "ok"
    except Exception as e:  # noqa: BLE001 - queremos seguir para as outras fontes
        print(f"FALHOU ({type(e).__name__}: {e})")
        resultados[nome] = f"falhou: {e}"


def tamanho(p):
    return f"({p.stat().st_size / 1024:.0f} KB)"


def limite_municipio():
    p = PASTA / "limite_municipio.geojson"
    baixar(f"{MALHAS}?formato=application/vnd.geo%2Bjson&qualidade=maxima", p)
    return tamanho(p)


def limite_distritos():
    p = PASTA / "distritos.geojson"
    baixar(f"{MALHAS}?formato=application/vnd.geo%2Bjson&qualidade=maxima&intrarregiao=distrito", p)
    return tamanho(p)


def nomes_distritos():
    p = PASTA / "distritos.json"
    baixar(f"{LOCALIDADES}/distritos", p)
    nomes = [d["nome"] for d in json.loads(p.read_text(encoding="utf-8"))]
    return f"{nomes}"


def cnefe():
    pagina = baixar(CNEFE_DIR).decode("latin-1")
    arquivos = sorted(set(re.findall(rf'href="({COD_IBGE}[^"]*\.zip)"', pagina)))
    if not arquivos:
        raise RuntimeError(f"arquivo {COD_IBGE}_*.zip não encontrado em {CNEFE_DIR}")
    p = PASTA / arquivos[0]
    baixar(CNEFE_DIR + arquivos[0], p, timeout=900)
    return f"{arquivos[0]} {tamanho(p)}"


def osm():
    p = PASTA / "osm_leopoldina.json"
    corpo = urllib.parse.urlencode({"data": CONSULTA_OSM}).encode()
    ultimo = None
    for url in OVERPASS:
        try:
            baixar(url, p, dados=corpo, timeout=300)
            elems = json.loads(p.read_text(encoding="utf-8"))["elements"]
            tipos = {}
            for e in elems:
                t = e.get("tags", {})
                chave = ("place=" + t["place"]) if "place" in t else ("power=" + t["power"]) if "power" in t \
                    else "highway" if "highway" in t else "outro"
                tipos[chave] = tipos.get(chave, 0) + 1
            return f"{len(elems)} elementos {tamanho(p)} {dict(sorted(tipos.items()))}"
        except Exception as e:  # noqa: BLE001
            ultimo = e
    raise ultimo


def main():
    PASTA.mkdir(parents=True, exist_ok=True)
    print(f"Baixando dados públicos de Leopoldina (IBGE {COD_IBGE}) para {PASTA}\n")
    resultados = {}
    passo("IBGE - limite do município", limite_municipio, resultados)
    passo("IBGE - limite dos distritos", limite_distritos, resultados)
    passo("IBGE - nomes dos distritos", nomes_distritos, resultados)
    passo("IBGE - CNEFE 2022 (endereços)", cnefe, resultados)
    passo("OpenStreetMap - localidades, estradas e rede", osm, resultados)

    falhas = [k for k, v in resultados.items() if v != "ok"]
    print("\nResumo:", "tudo baixado." if not falhas else f"{len(falhas)} fonte(s) com falha: {falhas}")
    if falhas:
        print("Dica: confira proxy/firewall; depois é só rodar de novo.")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
