# Fazenda por fazenda, ou uma comunidade só?

MVP para distribuidora de energia: transforma a lista de **clientes** afetados por um
desligamento programado em uma lista de **comunidades** afetadas, encurtando o aviso de rádio.

Usa apenas **dados sintéticos** (município fictício), com a verdade de campo conhecida
para medir a acurácia.

## Como rodar

```bash
pip install -r requirements.txt
python gerar_cenario.py          # gera dados/*.csv
python mvp_aviso.py CH-101       # ou AL-01, AL-02, AL-03, CH-102, CH-201, CH-301, CH-302
python mapa_rede.py              # mapa interativo da rede completa
python mapa_rede.py CH-101       # mapa interativo de um desligamento + aviso gerado
```

Abra `saidas/mapa_rede.html` (ou `saidas/mapa_rede_ch_101.html`) no navegador.

Saídas em `saidas/`: texto do aviso (atual × novo), mapa PNG e
`clientes_com_comunidade.csv` (cliente → comunidade prevista + confiança).

## Cenário (`gerar_cenario.py`)

- 8 comunidades rurais, 30 sítios isolados, 223 clientes;
- rede: subestação "SE Cascalho", 3 alimentadores (tronco seguindo as estradas),
  5 chaves de ramal, ~98 transformadores e ~106 trechos de rede MT
  (ramais traçados por árvore geradora mínima entre a chave e seus transformadores);
- problemas reais inseridos de propósito:
  - endereços sujos ("Sta. Rita", "Agua Lipma", "Cór. Fundo") e ~35% sem nome da comunidade;
  - ~5% das UCs sem coordenada (herdam a do transformador);
  - base oficial imperfeita: sem Cachoeirinha, Barreiro deslocado 1,2 km, "Fazenda Velha" sem clientes;
  - Santa Rita atendida por dois ramais (testa "parte da comunidade").

## Mapa da rede (`mapa_rede.py`)

Mapa interativo (Folium/Leaflet) com camadas que podem ser ligadas e desligadas:
subestação, troncos e ramais por alimentador, chaves, transformadores, clientes
(coloridos pela comunidade identificada), ligações cliente→trafo, contorno das
comunidades, localidades oficiais e estradas. Fundo: mapa claro, OpenStreetMap ou satélite.

Ao passar uma chave ou alimentador, a rede desligada e os clientes afetados ficam
em vermelho, e um painel mostra o aviso de rádio gerado, com a redução de tempo.

## Pipeline (`mvp_aviso.py`)

1. Completa coordenadas ausentes com a do transformador.
2. Extrai o nome da comunidade do endereço (normalização + abreviações + similaridade).
3. Agrupa clientes por proximidade (DBSCAN, eps 350 m).
4. Nomeia cada grupo cruzando base oficial e endereços, com confiança (alta / média / baixa → revisão humana).
5. Monta o aviso: comunidade inteira (≥ 70% afetada), "parte da comunidade X (lado ...)",
   e sítios isolados agrupados por trecho de estrada.

## Resultado atual

| Desligamento | Aviso atual | Aviso novo | Redução |
|---|---|---|---|
| AL-01 | 562 palavras (~3 min 45 s) | 56 palavras (~22 s) | 90% |
| CH-101 | 283 palavras (~1 min 53 s) | 64 palavras (~26 s) | 77% |
| CH-201 | 327 palavras (~2 min 11 s) | 51 palavras (~20 s) | 84% |

Acurácia cliente → comunidade: 100% (o cenário ainda é "fácil").

## Próximos passos

- Endurecer o cenário (comunidades vizinhas quase encostadas, comunidades lineares ao longo de estrada, mais endereços sem nome).
- Tela em Streamlit + Folium para o operador escolher a chave, revisar o mapa e o aviso.
- Dicionário de comunidades alimentado pelas correções do operador.
