"""
Agrupa as residências (UCs) por comunidade, independente de desligamento.

Usa o mesmo pipeline do MVP (coordenadas, nomes extraídos dos endereços,
DBSCAN e nomeação com confiança) e gera:

  saidas/residencias_por_comunidade.csv  cada residência com a sua comunidade
  saidas/comunidades.csv                 resumo por comunidade
  saidas/mapa_comunidades.html           mapa com o contorno e as residências de cada comunidade

Os CSVs usam ";" e UTF-8 com BOM, para abrir direto no Excel.

Uso:
    python agrupar_comunidades.py
"""

import html
import math
from collections import Counter

import folium
import numpy as np
import pandas as pd
from folium.plugins import Fullscreen
from scipy.spatial import ConvexHull

from mapa_rede import CORES_COMUNIDADE, carregar, popup
from mvp_aviso import SAIDAS

ISOLADA = "(sem comunidade)"
MARGEM_CONTORNO_M = 80  # folga do contorno em volta das residências da borda


def metros_para_latlon(x, y, lat0):
    return y / 111_320.0, x / (111_320.0 * math.cos(math.radians(lat0)))


def contorno(membros, lat0):
    """Contorno convexo das residências, com uma folga em metros (lista de [lat, lon])."""
    xy = membros[["x", "y"]].values
    if len(xy) < 3:
        return None, 0.0
    casca = ConvexHull(xy)
    centro = xy.mean(axis=0)
    pts = []
    for v in xy[casca.vertices]:
        d = v - centro
        v = v + d / (np.linalg.norm(d) or 1) * MARGEM_CONTORNO_M
        pts.append(list(metros_para_latlon(*v, lat0)))
    return pts, casca.volume / 1e6  # em 2D, "volume" é a área (m² -> km²)


def grafias(membros, nome):
    """Formas como o nome da comunidade aparece escrito nos endereços."""
    brutos = membros.loc[membros["nome_extraido"] == nome, "endereco"].str.split(",", n=1).str[1].str.strip()
    return "; ".join(f"{g} ({n})" for g, n in Counter(brutos).most_common())


def contagem(serie):
    return ", ".join(f"{k or 'tronco'} ({v})" for k, v in serie.value_counts().items())


def montar_tabelas(cli, grupos):
    lat0 = cli["lat"].mean()
    centros = grupos.set_index("grupo")[["cx", "cy"]]
    nomeado = cli["comunidade_prevista"] != "ISOLADO"

    res = cli.copy()
    res["comunidade"] = np.where(nomeado, res["comunidade_prevista"], ISOLADA)
    res["confianca"] = np.where(nomeado, res["confianca"], "-")

    # distância ao centro da própria comunidade; para isoladas, a comunidade mais próxima
    dist, mais_proxima = [], []
    for _, r in res.iterrows():
        if r["comunidade"] != ISOLADA:
            c = centros.loc[r["grupo"]]
            dist.append(round(math.hypot(r["x"] - c["cx"], r["y"] - c["cy"])))
            mais_proxima.append("")
        else:
            d = np.hypot(grupos["cx"] - r["x"], grupos["cy"] - r["y"])
            i = int(np.argmin(d))
            dist.append(round(float(d.iloc[i])))
            mais_proxima.append(grupos["nome"].iloc[i])
    res["distancia_centro_m"] = dist
    res["comunidade_mais_proxima"] = mais_proxima
    res["coordenada"] = np.where(res["coord_herdada"], "herdada do trafo", "própria")
    res["chave_id"] = res["chave_id"].replace("", "tronco")

    ordem = {n: i for i, n in enumerate(grupos.sort_values("clientes", ascending=False)["nome"])}
    res["_ordem"] = res["comunidade"].map(ordem).fillna(len(ordem))
    res = res.sort_values(["_ordem", "distancia_centro_m"])
    residencias = res[["comunidade", "confianca", "uc_id", "propriedade", "endereco", "nome_extraido",
                       "lat", "lon", "coordenada", "distancia_centro_m", "comunidade_mais_proxima",
                       "trafo_id", "chave_id", "alimentador"]].rename(
        columns={"nome_extraido": "nome_no_endereco"})

    linhas = []
    for _, g in grupos.sort_values("clientes", ascending=False).iterrows():
        m = cli[cli["grupo"] == g["grupo"]]
        _, area = contorno(m, lat0)
        la, lo = metros_para_latlon(g["cx"], g["cy"], lat0)
        linhas.append(dict(
            comunidade=g["nome"], residencias=len(m), confianca=g["confianca"], fonte_do_nome=g["fonte"],
            lat_centro=round(la, 6), lon_centro=round(lo, 6),
            raio_m=round(float(np.hypot(m["x"] - g["cx"], m["y"] - g["cy"]).max())),
            area_km2=round(area, 3),
            pct_com_nome_no_endereco=round(100 * m["nome_extraido"].eq(g["nome"]).mean()),
            grafias_encontradas=grafias(m, g["nome"]),
            alimentadores=contagem(m["alimentador"]), chaves=contagem(m["chave_id"]),
            transformadores=m["trafo_id"].nunique()))
    iso = cli[cli["comunidade_prevista"] == "ISOLADO"]
    linhas.append(dict(comunidade=ISOLADA, residencias=len(iso), confianca="-",
                       fonte_do_nome="residências fora de qualquer agrupamento",
                       alimentadores=contagem(iso["alimentador"]), chaves=contagem(iso["chave_id"]),
                       transformadores=iso["trafo_id"].nunique()))
    resumo = pd.DataFrame(linhas)
    for c in ["raio_m", "pct_com_nome_no_endereco"]:
        resumo[c] = resumo[c].astype("Int64")
    residencias = residencias.assign(lat=residencias["lat"].round(6), lon=residencias["lon"].round(6))
    return residencias, resumo


def painel(resumo, cores):
    itens = ""
    for _, c in resumo.iterrows():
        cor = cores.get(c["comunidade"], "#212529")
        itens += (f"<tr><td><span style='display:inline-block;width:10px;height:10px;border-radius:50%;"
                  f"background:{cor}'></span> {html.escape(c['comunidade'])}</td>"
                  f"<td style='text-align:right'><b>{c['residencias']}</b></td>"
                  f"<td style='color:#666;padding-left:8px'>{html.escape(str(c['confianca']))}</td></tr>")
    return f"""
    <div style="position:fixed;top:12px;right:56px;z-index:9999;background:white;padding:10px 14px;
                border-radius:8px;box-shadow:0 1px 6px rgba(0,0,0,.3);font:13px sans-serif;
                max-height:70vh;overflow:auto;max-width:calc(100vw - 80px)">
      <b>Residências por comunidade</b>
      <table style="margin-top:6px;border-collapse:collapse;line-height:1.7">
        <tr style="color:#666"><td>comunidade</td><td>resid.</td><td style="padding-left:8px">confiança</td></tr>
        {itens}
      </table>
      <div style="color:#888;margin-top:4px">Clique num contorno para ver a lista. Dados fictícios.</div>
    </div>"""


def salvar_mapa(cli, grupos, oficiais, resumo):
    lat0 = cli["lat"].mean()
    m = folium.Map(location=[cli["lat"].mean(), cli["lon"].mean()], zoom_start=12, tiles=None,
                   control_scale=True)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery", name="Satélite").add_to(m)

    cores = {g["nome"]: CORES_COMUNIDADE[i % len(CORES_COMUNIDADE)]
             for i, (_, g) in enumerate(grupos.iterrows())}
    camada_cont = folium.FeatureGroup(name="Contorno das comunidades")
    camada_res = folium.FeatureGroup(name="Residências")
    camada_of = folium.FeatureGroup(name="Localidades oficiais", show=False)

    for _, g in grupos.iterrows():
        membros = cli[cli["grupo"] == g["grupo"]]
        pts, area = contorno(membros, lat0)
        if pts is None:
            continue
        lista = "".join(f"<li>{html.escape(p)}</li>" for p in sorted(membros["propriedade"]))
        conteudo = (f"<div style='font:12px sans-serif;max-height:260px;overflow:auto'>"
                    f"<b>{html.escape(g['nome'])}</b> · {len(membros)} residências<br>"
                    f"confiança: <b>{g['confianca']}</b> ({html.escape(g['fonte'])})<br>"
                    f"área ≈ {area:.2f} km²<ol style='padding-left:18px;margin:6px 0'>{lista}</ol></div>")
        folium.Polygon(pts, color=cores[g["nome"]], weight=2, fill=True, fill_opacity=0.15,
                       popup=folium.Popup(conteudo, max_width=340),
                       tooltip=f"{g['nome']}: {len(membros)} residências").add_to(camada_cont)
        la, lo = metros_para_latlon(g["cx"], g["cy"], lat0)
        folium.Marker((max(p[0] for p in pts), lo), icon=folium.DivIcon(
            icon_size=(160, 20), icon_anchor=(80, 22),
            html=f"<div style='font:bold 12px sans-serif;text-align:center;color:{cores[g['nome']]};"
                 f"text-shadow:0 0 3px white,0 0 3px white'>{html.escape(g['nome'])} ({len(membros)})</div>"),
        ).add_to(camada_cont)

    for _, r in cli.iterrows():
        isolada = r["comunidade_prevista"] == "ISOLADO"
        cor = "#212529" if isolada else cores[r["comunidade_prevista"]]
        folium.CircleMarker(
            (r["lat"], r["lon"]), radius=4, color="white", weight=1, fill=True, fill_color=cor, fill_opacity=0.95,
            tooltip=r["propriedade"],
            popup=popup(r["uc_id"], {"propriedade": r["propriedade"], "endereço": r["endereco"],
                                     "comunidade": ISOLADA if isolada else r["comunidade_prevista"],
                                     "nome no endereço": r["nome_extraido"] if isinstance(r["nome_extraido"], str) else "-",
                                     "transformador": r["trafo_id"]}),
        ).add_to(camada_res)

    for _, o in oficiais.iterrows():
        folium.CircleMarker((o["lat"], o["lon"]), radius=7, color="red", weight=2, fill=False,
                            tooltip=f"Oficial: {o['nome']}").add_to(camada_of)

    for c in (camada_cont, camada_res, camada_of):
        c.add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)
    Fullscreen().add_to(m)
    m.get_root().html.add_child(folium.Element(painel(resumo, cores)))
    m.get_root().header.add_child(folium.Element("<title>Residências por comunidade</title>"))
    m.fit_bounds([cli[["lat", "lon"]].min().tolist(), cli[["lat", "lon"]].max().tolist()])
    caminho = SAIDAS / "mapa_comunidades.html"
    m.save(caminho)
    return caminho


def main():
    cli, _, oficiais, _, _, _, grupos = carregar()
    residencias, resumo = montar_tabelas(cli, grupos)

    SAIDAS.mkdir(exist_ok=True)
    residencias.to_csv(SAIDAS / "residencias_por_comunidade.csv", sep=";", index=False, encoding="utf-8-sig")
    resumo.to_csv(SAIDAS / "comunidades.csv", sep=";", index=False, encoding="utf-8-sig")
    mapa = salvar_mapa(cli, grupos, oficiais, resumo)

    print("Residências por comunidade:\n")
    print(resumo[["comunidade", "residencias", "confianca", "raio_m", "pct_com_nome_no_endereco", "chaves"]]
          .rename(columns={"pct_com_nome_no_endereco": "% c/ nome no end."})
          .astype(object).fillna("-").to_string(index=False))

    # conferência com a verdade de campo do cenário sintético
    real = cli["comunidade_real"].replace("ISOLADO", ISOLADA)
    prev = cli["comunidade_prevista"].replace("ISOLADO", ISOLADA)
    erros = cli[real != prev]
    print(f"\nConferência com a verdade de campo: {len(cli) - len(erros)}/{len(cli)} residências "
          f"no grupo certo ({1 - len(erros) / len(cli):.1%})")
    if len(erros):
        print(erros[["uc_id", "endereco", "comunidade_real", "comunidade_prevista"]].to_string(index=False))

    print(f"\nArquivos gerados:\n  {SAIDAS / 'residencias_por_comunidade.csv'}\n"
          f"  {SAIDAS / 'comunidades.csv'}\n  {mapa}")


if __name__ == "__main__":
    main()
