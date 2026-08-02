"""Arquivos de teste para as conversas: planilha, PDF e imagem.

Os números são escolhidos a dedo e conferidos pelo harness: se o modelo
inventar em vez de ler o anexo, a diferença aparece na hora.

Uso:
    LICITA_FIXTURES=<pasta> python scripts/gerar_fixtures_qa.py
"""

import os
from pathlib import Path

import openpyxl
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

DEST = Path(os.environ.get("LICITA_FIXTURES", "docs/fixtures-qa"))
DEST.mkdir(parents=True, exist_ok=True)

# ---- planilha com dados verificáveis -----------------------------------
# Números escolhidos a dedo: se o agente inventar, a diferença aparece.

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "execucao_2025"
ws.append(["municipio", "uf", "mes", "empenhado", "liquidado", "pago"])
LINHAS = [
    ("Porto Velho", "RO", 1, 1_240_000.00, 980_000.00, 910_000.00),
    ("Porto Velho", "RO", 2, 1_310_500.50, 1_100_000.00, 1_050_000.00),
    ("Porto Velho", "RO", 3, 1_455_000.00, 1_280_000.00, 1_190_000.00),
    ("Porto Velho", "RO", 4, 1_198_750.25, 1_050_000.00, 1_010_000.00),
    ("Campinas", "SP", 1, 8_420_000.00, 7_900_000.00, 7_610_000.00),
    ("Campinas", "SP", 2, 8_915_300.75, 8_200_000.00, 7_990_000.00),
    ("Campinas", "SP", 3, 9_530_000.00, 8_870_000.00, 8_540_000.00),
    ("Campinas", "SP", 4, 8_105_200.10, 7_640_000.00, 7_400_000.00),
    ("Ji-Paraná", "RO", 1, 640_000.00, 590_000.00, 560_000.00),
    ("Ji-Paraná", "RO", 2, 712_400.00, 660_000.00, 630_000.00),
    ("Ji-Paraná", "RO", 3, 688_900.00, 640_000.00, 615_000.00),
    ("Ji-Paraná", "RO", 4, 701_150.40, 655_000.00, 628_000.00),
]
for linha in LINHAS:
    ws.append(list(linha))
caminho_xlsx = DEST / "execucao_orcamentaria_2025.xlsx"
wb.save(caminho_xlsx)

TOTAL_EMPENHADO = sum(linha[3] for linha in LINHAS)
PV_EMPENHADO = sum(linha[3] for linha in LINHAS if linha[0] == "Porto Velho")

# ---- PDF de edital -----------------------------------------------------

caminho_pdf = DEST / "edital_pregao_042_2025.pdf"
c = canvas.Canvas(str(caminho_pdf), pagesize=A4)
largura, altura = A4


def pagina(titulo, linhas):
    c.setFont("Helvetica-Bold", 14)
    c.drawString(60, altura - 70, titulo)
    c.setFont("Helvetica", 10.5)
    y = altura - 105
    for texto in linhas:
        c.drawString(60, y, texto)
        y -= 17
    c.showPage()


pagina(
    "EDITAL DE PREGAO ELETRONICO N. 042/2025",
    [
        "Municipio de Porto Velho - RO",
        "Objeto: aquisicao de equipamentos de informatica para as escolas",
        "da rede municipal de ensino.",
        "",
        "Valor total estimado: R$ 2.480.750,00",
        "Data de abertura: 15 de setembro de 2025, as 09h00.",
        "Modo de disputa: aberto.",
        "Prazo de entrega: 45 dias corridos apos a ordem de fornecimento.",
    ],
)
pagina(
    "ITENS LICITADOS",
    [
        "Item 1 - Notebook 16GB RAM, 512GB SSD - 120 unidades - R$ 4.200,00 cada",
        "Item 2 - Projetor multimidia 3500 lumens - 40 unidades - R$ 2.850,00 cada",
        "Item 3 - Roteador wifi 6 corporativo - 85 unidades - R$ 1.190,00 cada",
        "Item 4 - Tablet educacional 10 polegadas - 300 unidades - R$ 1.650,00 cada",
        "",
        "Prazo de garantia minimo: 36 meses para todos os itens.",
    ],
)
pagina(
    "HABILITACAO E PENALIDADES",
    [
        "Exige-se certidao negativa de debitos federais, estaduais e municipais.",
        "Atestado de capacidade tecnica compativel com 50% do quantitativo.",
        "Multa por atraso: 0,5% ao dia sobre o valor do item, limitada a 10%.",
        "Impedimento de licitar por ate 2 anos em caso de inexecucao total.",
    ],
)
c.save()

# ---- imagem com número legível ----------------------------------------

caminho_img = DEST / "painel_licitacoes.png"
img = Image.new("RGB", (900, 500), "white")
d = ImageDraw.Draw(img)
d.rectangle([0, 0, 900, 80], fill=(31, 58, 95))
d.text((30, 32), "PAINEL DE LICITACOES - PORTO VELHO / RO", fill="white")
barras = [("2022", 180), ("2023", 240), ("2024", 310), ("2025", 265)]
x = 90
for rotulo, altura_barra in barras:
    d.rectangle([x, 430 - altura_barra, x + 110, 430], fill=(45, 120, 190))
    d.text((x + 35, 440), rotulo, fill="black")
    d.text((x + 20, 415 - altura_barra), str(altura_barra), fill="black")
    x += 190
d.text((30, 470), "Numero de processos licitatorios abertos por ano", fill=(60, 60, 60))
img.save(caminho_img)

print("planilha:", caminho_xlsx)
print("  total empenhado:", round(TOTAL_EMPENHADO, 2))
print("  Porto Velho empenhado:", round(PV_EMPENHADO, 2))
print("pdf:", caminho_pdf)
print("imagem:", caminho_img)
print("dir:", DEST)
