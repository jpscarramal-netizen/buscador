# Buscador

Busca instantânea de arquivos para Windows, inspirada no Everything, com o que faltava para quem estuda
por PDF: **prévia com texto copiável**, **busca dentro dos PDFs** (mostra a página e o trecho), **OCR** de
PDF escaneado, **recorte de imagem** da página e **achar arquivos duplicados**.

Tudo roda no seu computador. Nada é enviado para a internet.

## Instalar

1. Baixe o `Buscador-windows.zip` da última versão em **[Releases](../../releases/latest)**.
2. Extraia a pasta para um lugar fixo (por exemplo `C:\Programas\Buscador`).
3. Abra o `Buscador.exe`.

O Windows pode mostrar o aviso azul "O Windows protegeu o computador", porque o programa não tem assinatura
digital paga. Clique em **Mais informações** e depois em **Executar assim mesmo**.

Na primeira vez o Buscador lê os nomes de todos os arquivos dos discos (uns minutos). Depois abre na hora.
O texto dos PDFs é lido em segundo plano, com prioridade baixa; em um computador com 15 mil PDFs leva cerca
de 2 horas, uma vez só.

Para abrir junto com o Windows: menu **Visualização > Abrir junto com o Windows**.
Para desinstalar: desmarque essa opção, apague a pasta do programa e as pastas de dados listadas abaixo.

Requisitos: Windows 10 ou 11, 64 bits.

## O que faz

- **Busca nos nomes** igual ao Everything (`aula cirurgia`, `aula|prova`, `!temp`, `*.pdf`, `ext:pdf;docx`,
  `pasta:`, termo com `\` procura no caminho), sem diferença de acento (`cancer` acha "Câncer").
- **Siglas e sinônimos**: `iam` também acha "infarto agudo do miocárdio". A lista é um arquivo de texto
  editável (Ferramentas > Editar sinônimos).
- **Data e tamanho**: `dm:hoje`, `dm:semana`, `dm:01/10/2026..15/10/2026`, `tamanho:>10mb`.
- **Busca dentro dos arquivos** (PDF, Word, PowerPoint, texto): botão **Conteúdo** ou Ctrl+D. Cada resultado
  mostra a página e o trecho; Enter abre o PDF já na página.
- **Prévia** de PDF (com seleção de texto por arraste), Word, PowerPoint, Excel, texto e imagem.
  - **✂ Recortar** (ou Shift + arrastar): copia uma área da página como imagem, para colar no Anki ou no Word.
  - **Com fonte**: o texto copiado leva "(Fonte: arquivo, p. 12)".
- **OCR** de PDF escaneado com o OCR que já vem no Windows, em português (botão "Ler com OCR").
- **Favoritos e histórico** de buscas (botão ★ ou Ctrl+B).
- **Abertos recentemente** aparecem no topo, em negrito.
- **Arrastar** arquivo da lista para o WhatsApp, o e-mail ou uma pasta.
- **Duplicados**: Ferramentas > Achar arquivos duplicados (mesmo nome e tamanho); manda as cópias para a Lixeira.
- **Alt+Espaço** abre e esconde o Buscador de qualquer lugar (Ctrl+Alt+Espaço se outro programa já usa).

F1 dentro do programa mostra toda a sintaxe e os atalhos.

## Onde ficam os dados

- Índice de nomes, preferências e sinônimos: `%LOCALAPPDATA%\BuscadorLocal`.
- Índice do texto dos arquivos e cache do OCR: `BuscadorLocal` no disco com mais espaço livre (fora do disco
  do Windows quando há outro disco).

## Problema?

Abra uma [issue](../../issues) com o resultado de:

```
Buscador.exe --diagnostico > diagnostico.txt
```

## Rodar pelo código

Precisa de Python 3.12 no Windows.

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pythonw Buscador.pyw
```

Gerar o .exe: `.venv\Scripts\pip install pyinstaller` e `.venv\Scripts\pyinstaller Buscador.spec`
(sai em `dist\Buscador`).

Código em `buscador/`: `indice.py` (varredura e índice de nomes), `consulta.py` (sintaxe), `conteudo.py`
(índice do texto, SQLite FTS5), `extrair.py` (texto de cada tipo), `ocr.py`, `previa.py` e `paginas.py`
(prévia e visor de PDF), `janela.py` (interface), `duplicados.py`, `abrir.py`, `config.py`, `sinonimos.py`.

## Licença

AGPL-3.0 (exigida pelo PyMuPDF, que lê os PDFs). Projeto independente, sem ligação com a voidtools,
autora do Everything.
