"""
Mapa interativo (HTML) da rede de distribuição do cenário fictício.

Camadas: subestação, alimentadores (tronco e ramais), chaves, transformadores,
clientes (coloridos pela comunidade identificada), ligações cliente→trafo,
contorno das comunidades, localidades oficiais e estradas.

Com um equipamento (chave ou alimentador), destaca a rede desligada, os clientes
afetados e mostra o aviso de rádio gerado.

Uso:
    python mapa_rede.py              # rede completa  -> saidas/mapa_rede.html
    python mapa_rede.py CH-101       # desligamento   -> saidas/mapa_rede_ch_101.html
"""

import html
import sys

import folium
import pandas as pd
from folium.plugins import Fullscreen, MeasureControl
from scipy.spatial import ConvexHull

from gerar_cenario import ESTRADAS, km_para_latlon
from mvp_aviso import (DADOS, SAIDAS, agrupar_e_nomear, completar_coordenadas, contar_palavras,
                       duracao, extrair_nomes, montar_avisos)

COR_ALIMENTADOR = {"AL-01": "#1f6feb", "AL-02": "#8250df", "AL-03": "#1a7f37"}
COR_DESLIGADO = "#d1242f"
CORES_COMUNIDADE = ["#e8590c", "#2b8a3e", "#1971c2", "#9c36b5", "#c2255c",
                    "#0c8599", "#5c940d", "#e67700", "#364fc7", "#a61e4d"]


def carregar():
    cli = pd.read_csv(DADOS / "clientes.csv")
    trafos = pd.read_csv(DADOS / "trafos.csv", keep_default_na=False)
    trafos[["lat", "lon"]] = trafos[["lat", "lon"]].astype(float)
    oficiais = pd.read_csv(DADOS / "localidades_oficiais.csv")
    chaves = pd.read_csv(DADOS / "chaves.csv")
    redes = pd.read_csv(DADOS / "redes_mt.csv", keep_default_na=False)
    for c in ["lat1", "lon1", "lat2", "lon2"]:
        redes[c] = redes[c].astype(float)
    se = pd.read_csv(DADOS / "subestacoes.csv")

    cli = completar_coordenadas(cli, trafos)
    cli["nome_extraido"] = extrair_nomes(cli, oficiais)
    cli, grupos = agrupar_e_nomear(cli, oficiais)
    return cli, trafos, oficiais, chaves, redes, se, grupos


def desligado(df, equipamento):
    if not equipamento:
        return pd.Series(False, index=df.index)
    return (df["chave_id"] == equipamento) | (df["alimentador"] == equipamento)


def popup(titulo, campos):
    linhas = "".join(f"<tr><td style='color:#666;padding-right:8px'>{html.escape(k)}</td>"
                     f"<td><b>{html.escape(str(v))}</b></td></tr>" for k, v in campos.items())
    return folium.Popup(f"<div style='font-family:sans-serif;font-size:12px'><b>{html.escape(titulo)}</b>"
                        f"<table>{linhas}</table></div>", max_width=320)


def legenda(equipamento):
    itens = "".join(f"<div><span style='display:inline-block;width:18px;height:4px;background:{c};"
                    f"vertical-align:middle;margin-right:6px'></span>Alimentador {a}</div>"
                    for a, c in COR_ALIMENTADOR.items())
    extra = (f"<div><span style='display:inline-block;width:18px;height:4px;background:{COR_DESLIGADO};"
             f"vertical-align:middle;margin-right:6px'></span>Rede desligada ({equipamento})</div>"
             if equipamento else "")
    return f"""
    <div style="position:fixed;bottom:24px;left:12px;z-index:9999;background:white;padding:10px 12px;
                border-radius:8px;box-shadow:0 1px 6px rgba(0,0,0,.3);font:12px sans-serif;line-height:1.7">
      <b>Legenda</b>{itens}{extra}
      <div>&#9889; Subestação &nbsp; &#9632; Chave &nbsp; &#9650; Transformador</div>
      <div>&#9679; Cliente (cor = comunidade; preto = isolado)</div>
      <div style="color:#888">Dados 100% fictícios</div>
    </div>"""


def painel_aviso(equipamento, afetados, atual, novo):
    pa, pn = contar_palavras(atual), contar_palavras(novo)
    return f"""
    <div style="position:fixed;top:12px;right:56px;z-index:9999;width:360px;max-width:calc(100vw - 80px);
                background:white;padding:12px 14px;border-radius:8px;box-shadow:0 1px 6px rgba(0,0,0,.3);
                font:13px sans-serif;max-height:60vh;overflow:auto">
      <b style="color:{COR_DESLIGADO}">Desligamento {equipamento}</b> · {len(afetados)} clientes afetados
      <div style="margin:8px 0;padding:8px;background:#f6f8fa;border-left:3px solid {COR_DESLIGADO}">
        {html.escape(novo)}</div>
      <div>Aviso atual: <b>{pa}</b> palavras (~{duracao(pa)})<br>
           Aviso novo: <b>{pn}</b> palavras (~{duracao(pn)})<br>
           Redução: <b>{1 - pn / pa:.0%}</b> no tempo de rádio</div>
    </div>"""


def main():
    equipamento = sys.argv[1].upper() if len(sys.argv) > 1 else None
    cli, trafos, oficiais, chaves, redes, se, grupos = carregar()

    centro = [cli["lat"].mean(), cli["lon"].mean()]
    m = folium.Map(location=centro, zoom_start=12, tiles=None, control_scale=True)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Light Gray", name="Mapa claro").add_to(m)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery", name="Satélite").add_to(m)

    camadas = {nome: folium.FeatureGroup(name=nome, show=show) for nome, show in [
        ("Estradas", True), ("Comunidades (contorno)", True), ("Rede MT – tronco", True),
        ("Rede MT – ramais", True), ("Ligações cliente→trafo", False), ("Transformadores", True),
        ("Clientes", True), ("Chaves", True), ("Subestação", True), ("Localidades oficiais", True)]}

    # estradas
    for nome, pts in ESTRADAS.items():
        folium.PolyLine([km_para_latlon(*p) for p in pts], color="#adb5bd", weight=3, dash_array="6 6",
                        tooltip=nome).add_to(camadas["Estradas"])

    # comunidades: contorno convexo + rótulo
    cor_grupo = {g: CORES_COMUNIDADE[i % len(CORES_COMUNIDADE)] for i, g in enumerate(grupos["grupo"])}
    for _, g in grupos.iterrows():
        pts = cli.loc[cli["grupo"] == g["grupo"], ["lat", "lon"]].values
        if len(pts) >= 3:
            casca = pts[ConvexHull(pts).vertices]
            folium.Polygon(casca.tolist(), color=cor_grupo[g["grupo"]], weight=1.5, fill=True, fill_opacity=0.08,
                           popup=popup(g["nome"], {"clientes": g["clientes"], "confiança": g["confianca"],
                                                   "fonte do nome": g["fonte"]}),
                           tooltip=f"{g['nome']} [{g['confianca']}]").add_to(camadas["Comunidades (contorno)"])

    # rede MT
    redes["off"] = desligado(redes, equipamento)
    for _, r in redes.iterrows():
        tronco = r["tipo"] == "tronco"
        cor = COR_DESLIGADO if r["off"] else COR_ALIMENTADOR[r["alimentador"]]
        folium.PolyLine([(r["lat1"], r["lon1"]), (r["lat2"], r["lon2"])], color=cor,
                        weight=5 if tronco else 2.5, opacity=0.9,
                        tooltip=f"{r['trecho_id']} · {r['alimentador']}"
                                + (f" · {r['chave_id']}" if r["chave_id"] else "") + f" · {r['tipo']}",
                        ).add_to(camadas["Rede MT – tronco" if tronco else "Rede MT – ramais"])

    # ligações cliente -> trafo (baixa tensão)
    for _, c in cli.iterrows():
        folium.PolyLine([(c["lat"], c["lon"]), (c["lat_trafo"], c["lon_trafo"])], color="#868e96",
                        weight=1).add_to(camadas["Ligações cliente→trafo"])

    # transformadores
    n_por_trafo = cli.groupby("trafo_id").size()
    trafos["off"] = desligado(trafos, equipamento)
    for _, t in trafos.iterrows():
        cor = COR_DESLIGADO if t["off"] else COR_ALIMENTADOR[t["alimentador"]]
        folium.RegularPolygonMarker((t["lat"], t["lon"]), number_of_sides=3, radius=6, rotation=30,
                                    color=cor, fill=True, fill_color=cor, fill_opacity=0.9, weight=1,
                                    popup=popup(t["trafo_id"], {"alimentador": t["alimentador"],
                                                                "chave": t["chave_id"] or "tronco",
                                                                "clientes": int(n_por_trafo.get(t["trafo_id"], 0))}),
                                    tooltip=t["trafo_id"]).add_to(camadas["Transformadores"])

    # clientes
    cli["off"] = desligado(cli, equipamento)
    for _, c in cli.iterrows():
        isolado = c["comunidade_prevista"] == "ISOLADO"
        cor = "#212529" if isolado else cor_grupo[c["grupo"]]
        campos = {"endereço": c["endereco"], "transformador": c["trafo_id"],
                  "comunidade identificada": c["comunidade_prevista"],
                  "confiança": c["confianca"] if not isolado else "-",
                  "coordenada": "herdada do trafo" if c["coord_herdada"] else "própria"}
        if equipamento:
            campos["situação"] = "SEM ENERGIA" if c["off"] else "normal"
        marcador = folium.CircleMarker(
            (c["lat"], c["lon"]), radius=5 if c["off"] or not equipamento else 3,
            color=COR_DESLIGADO if c["off"] else cor, weight=2 if c["off"] else 1,
            fill=True, fill_color=cor, fill_opacity=0.9 if (c["off"] or not equipamento) else 0.35,
            popup=popup(c["uc_id"], campos), tooltip=c["propriedade"])
        marcador.add_to(camadas["Clientes"])

    # chaves
    clientes_por_chave = cli.groupby("chave_id").size()
    for _, ch in chaves.iterrows():
        aberta = equipamento in (ch["chave_id"], ch["alimentador"])
        folium.RegularPolygonMarker((ch["lat"], ch["lon"]), number_of_sides=4, radius=9, rotation=45,
                                    color="black", weight=2, fill=True,
                                    fill_color=COR_DESLIGADO if aberta else "white", fill_opacity=1,
                                    popup=popup(ch["chave_id"], {"descrição": ch["descricao"],
                                                                 "alimentador": ch["alimentador"],
                                                                 "clientes a jusante": int(clientes_por_chave.get(ch["chave_id"], 0)),
                                                                 "estado": "ABERTA (desligamento)" if aberta else "fechada"}),
                                    tooltip=ch["chave_id"]).add_to(camadas["Chaves"])
        folium.Marker((ch["lat"], ch["lon"]), icon=folium.DivIcon(
            html=f"<div style='font:bold 11px sans-serif;margin:8px 0 0 10px;white-space:nowrap'>{ch['chave_id']}</div>")
        ).add_to(camadas["Chaves"])

    # subestação
    for _, s in se.iterrows():
        folium.Marker((s["lat"], s["lon"]), icon=folium.Icon(color="orange", icon="bolt", prefix="fa"),
                      popup=popup(s["nome"], {"alimentadores": s["alimentadores"]}),
                      tooltip=s["nome"]).add_to(camadas["Subestação"])

    # localidades oficiais (simula IBGE/OSM)
    for _, o in oficiais.iterrows():
        folium.Marker((o["lat"], o["lon"]), icon=folium.Icon(color="red", icon="map-marker", prefix="fa"),
                      popup=popup(o["nome"], {"tipo": o["tipo"], "fonte": "base oficial (simulada)"}),
                      tooltip=f"Oficial: {o['nome']}").add_to(camadas["Localidades oficiais"])

    for c in camadas.values():
        c.add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)
    Fullscreen().add_to(m)
    MeasureControl(primary_length_unit="kilometers").add_to(m)
    m.get_root().html.add_child(folium.Element(legenda(equipamento)))

    if equipamento:
        afetados, atual, novo = montar_avisos(cli, grupos, equipamento)
        m.get_root().html.add_child(folium.Element(painel_aviso(equipamento, afetados, atual, novo)))
        afetados_bounds = afetados[["lat", "lon"]].values
        m.fit_bounds([afetados_bounds.min(axis=0).tolist(), afetados_bounds.max(axis=0).tolist()])
    else:
        m.fit_bounds([cli[["lat", "lon"]].min().tolist(), cli[["lat", "lon"]].max().tolist()])

    titulo = f"Rede de distribuição – desligamento {equipamento}" if equipamento else "Rede de distribuição – cenário fictício"
    m.get_root().header.add_child(folium.Element(f"<title>{titulo}</title>"))

    SAIDAS.mkdir(exist_ok=True)
    nome = "mapa_rede" + (f"_{equipamento.lower().replace('-', '_')}" if equipamento else "") + ".html"
    m.save(SAIDAS / nome)
    print(f"Mapa salvo em {SAIDAS / nome}")


if __name__ == "__main__":
    main()
