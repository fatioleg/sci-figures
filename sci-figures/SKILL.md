---
name: sci-figures
description: "Figuras científicas reproduzíveis e com padrão de publicação a partir de especificações curtas: moléculas 2D (RDKit, estilo ACS 1996), estruturas de moléculas, dímeros, aglomerados, cristais e superfícies (ASE: bola-e-bastão ou esferas), fluxogramas e pipelines (D2) e gráficos de dados (matplotlib, estilo de periódico com paleta para daltônicos). Cada figura tem fig.yaml, fonte nativa, saídas SVG/PDF/PNG, manifesto de proveniência (hashes, versões, InChIKey) e verificação real (SMILES válido, resolução, rótulos, texto alternativo, proibição de imagem de IA em figura de dados). Use quando o usuário pedir figura de molécula, estrutura, geometria otimizada, superfície, fluxograma, diagrama de workflow, perfil de energia, gráfico para artigo ou relatório — mesmo sem dizer 'skill'."
---

# sci-figures — figuras por especificação, não por desenho

O agente escreve **só a especificação** (`fig.yaml` + fonte nativa). Ferramentas determinísticas desenham.
Não descreva figuras em linguagem natural para um gerador de imagens.

CLI: `python3 <skill>/scripts/figs.py [--dir figures] <comando>`. `<skill>` é a pasta deste arquivo. Na primeira vez, rode `setup` (cria o venv com RDKit, ASE, matplotlib e cairosvg) e `doctor`.

## Tipos e quando usar

| `kind` | Motor | Para | Fonte |
|---|---|---|---|
| `molecule-2d` | RDKit (modo ACS 1996) | estruturas químicas, séries de compostos, destaque de subestrutura | `molecules: [{smiles, legend}]`, `per_row`, `highlight_smarts` |
| `structure` | ASE + matplotlib | geometria otimizada, dímero, aglomerado (`ball-stick`); cristal, superfície, adsorção (`spacefill`) | `source:` xyz, cif, POSCAR, `.traj`, saída ORCA/CP2K/VASP (qualquer formato do `ase.io.read`; use `format:` se preciso), `index`, `rotation`, `repeat`, `show_cell`, `labels: symbols\|indices` |
| `diagram` | D2 (layout `elk`) | fluxograma, pipeline, arquitetura, workflow de agentes | `diagram.d2` |
| `plot` | matplotlib + `uah-pub.mplstyle` | dados reais: perfil de energia, convergência, espectros, método × referência | `plot.py` (usa `plt` e `save(fig)` injetados) + `inputs: [dados.csv]` |

Fora do automático (manual ou premium, só para figura-chave): **VESTA** (isossuperfícies de `.cube`, cristais), **PyMOL** open-source (biomoléculas, orbitais por script), **ChimeraX** (licença acadêmica), **Blender + Molecular Nodes** (capa e divulgação), **Inkscape** (polimento final de SVG). Para 3D interativo, perfis de energia ao vivo e modos vibracionais, use o **CalcBoard**.

## Fluxo

1. `figs.py new <id> --kind <tipo>`. Isso cria `figures/<id>/` com `fig.yaml` e uma fonte de exemplo. O `id` vira `#fig-<id>` no Quarto.
2. Edite `fig.yaml`. Obrigatórios:
   - `title`: legenda curta;
   - `alt`: texto alternativo descritivo;
   - `width_mm`: 85 para coluna simples, 170 a 178 para dupla;
   - `dpi`: 300 por padrão.

   Figura de dados (`plot`, `structure`) é `data_figure: true` por padrão. Declare os arquivos de dados em `inputs:`.
3. `figs.py render [ids]` gera `<id>.svg`, `.pdf` e `.png`.
4. `figs.py verify [ids]` dá FAIL (e código de saída 1) quando:
   - a especificação é inválida ou não há figura nenhuma na pasta;
   - alguma fonte (`fig.yaml`, fonte nativa, `inputs:`) mudou desde o último render (comparação por hash);
   - o PNG tem menos pixels do que `width_mm` × `dpi` exige;
   - há SMILES inválido (os válidos têm fórmula e InChIKey registrados);
   - falta no SVG algum rótulo do diagrama;
   - um gráfico de dados não declara `inputs:`;
   - um `plot` ou `structure` tem `ai_generated`.

   Grava `figures/<id>/manifest.json` (hashes, versões, resultado de cada checagem, `passed`) e monta `figures/_review/contato.png`.
5. **Inspeção visual:** abra (Read) `figures/_review/contato.png` e, se preciso, a figura em tamanho real. Confira:
   - orientação e legibilidade;
   - sobreposição de átomos ou rótulos;
   - cores distinguíveis;
   - unidades nos eixos.
6. **Incluir no documento:** `figs.py qmd <id> --relative-to publication` imprime o trecho Quarto com o texto alternativo. O `<id>.pdf` vetorial ao lado do SVG é reaproveitado pelo LaTeX sem conversão extra (publish-documents).

## Regras

- **Nunca** use imagem gerada por IA para representar resultado ou dado. Diagramas conceituais podem existir, mas ficam identificados (`ai_generated: true` e `data_figure: false`, com aviso na legenda).
- Figura de dados vem de arquivo de dados versionado (`inputs:`) e de script (`plot.py`). Nada de números digitados no gráfico sem fonte.
- Estilo único por documento: mesma largura de coluna, mesma fonte, mesma paleta (`uah-pub.mplstyle`: Okabe-Ito).
- Para orientar uma estrutura, ajuste `rotation` (ex.: `"-75x,0y,15z"`) e renderize de novo. É barato. Não tente "descrever" a vista.
- Custo: gere e verifique só as figuras alteradas (`render <id>`). A folha de contato evita abrir figura por figura.

## Com o UAH e o publish-documents

- Instale com `uah.py integrations install sci-figures` (ou ative com `integrations enable sci-figures` se a skill já estiver instalada). O `setup` (instala pacotes) pede confirmação; antes da aprovação do plano só roda o `doctor`.
- Em fatias, critérios de aceite típicos:
  - `AC: verify sem FAIL`;
  - `AC: contato.png inspecionado`;
  - `AC: toda figura de dados com inputs declarados`.
