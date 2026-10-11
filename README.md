# sci-figures

> **Figuras por especificação, não por desenho.** Uma skill do [Claude Code](https://claude.com/claude-code)
> em que o agente escreve só uma especificação curta (`fig.yaml` + fonte nativa) e ferramentas determinísticas
> desenham moléculas, estruturas, fluxogramas e gráficos com padrão de publicação.

Integra o fluxo do [Universal Agent Harness](https://github.com/fatioleg/Universal-Agent-Harness) como a
integração `sci-figures` e entrega SVG/PDF prontos para o [publish-documents](https://github.com/fatioleg/publish-documents).

## Tipos

| `kind` | Motor | Para |
|---|---|---|
| `molecule-2d` | RDKit (estilo ACS 1996) | estruturas químicas, séries de compostos, destaque de subestrutura |
| `structure` | ASE + matplotlib | geometrias otimizadas, dímeros e aglomerados (bola-e-bastão); cristais, superfícies e adsorção (esferas) |
| `diagram` | D2 (layout elk) | fluxogramas, pipelines, arquitetura, workflows de agentes |
| `plot` | matplotlib + `uah-pub.mplstyle` | dados reais: perfis de energia, convergência, espectros, método × referência |

Cada figura vive em `figures/<id>/`:

- `fig.yaml`: tipo, legenda, texto alternativo, largura, dpi;
- fonte nativa: SMILES no próprio `fig.yaml`, `diagram.d2`, geometria (`xyz`, `cif`, `POSCAR`, saída ORCA/CP2K/VASP) ou `plot.py` com os dados em `inputs:`;
- `<id>.svg`, `.pdf` e `.png`: saídas geradas, nunca editadas à mão;
- `manifest.json`: hashes de fontes e saídas, versões das ferramentas, fórmula e InChIKey, resultado das checagens.

## Comandos

```bash
F=~/.claude/skills/sci-figures/scripts/figs.py
python3 $F setup                              # cria o venv com RDKit, ASE, matplotlib, cairosvg
python3 $F doctor                             # testa bibliotecas, D2 e opcionais (PyMOL, VESTA, POV-Ray)
python3 $F new energia --kind plot            # cria figures/energia/ com um exemplo
python3 $F render                             # gera SVG, PDF e PNG
python3 $F verify                             # checagens + manifest.json + folha de contato
python3 $F qmd energia --relative-to publication   # trecho Quarto com #fig-energia e fig-alt
```

O `verify` dá FAIL (código de saída 1) quando:

- a especificação é inválida ou a pasta não tem figuras;
- alguma fonte mudou desde o último render (comparação por hash, não por data);
- o PNG tem menos pixels do que `width_mm` × `dpi` exige;
- há SMILES inválido;
- falta no SVG algum rótulo do diagrama;
- um gráfico de dados não declara arquivo de dados;
- uma figura de dados está marcada como gerada por IA.

Depois ele monta `figures/_review/contato.png` para a inspeção visual.

## Instalação

```bash
git clone git@github.com:fatioleg/sci-figures.git ~/.claude/tools/sci-figures
ln -s ~/.claude/tools/sci-figures/sci-figures ~/.claude/skills/sci-figures
python3 ~/.claude/skills/sci-figures/scripts/figs.py setup
```

O `setup` cria um ambiente isolado em `~/.local/share/sci-figures/venv` (ou em `$SCI_FIGURES_VENV`), sem
mexer no Python do sistema. A CLI passa a usá-lo sozinha. Para diagramas, instale o
[D2](https://d2lang.com/tour/install) ≥ 0.6. Ele exporta PNG e PDF sem navegador nem rede.

Com o UAH: `uah.py integrations install sci-figures`.

## Licenças das ferramentas

As figuras geradas pertencem a você. As licenças abaixo valem para as ferramentas, não para as imagens.

| Ferramenta | Licença | Uso aqui |
|---|---|---|
| RDKit | BSD-3-Clause | automático |
| ASE | LGPL-2.1+ | automático |
| matplotlib | licença matplotlib (estilo PSF/BSD) | automático |
| D2 | MPL-2.0 | automático |
| CairoSVG | LGPL-3.0 | automático (SVG → PDF/PNG) |
| Pillow · PyYAML | MIT-CMU · MIT | automático |
| PyMOL open-source | licença PyMOL (permissiva) | opcional, manual |
| VESTA | gratuito para uso acadêmico e não comercial | opcional, manual (isossuperfícies de `.cube`) |
| UCSF ChimeraX | gratuito para uso não comercial | opcional, manual |
| Blender + Molecular Nodes · Inkscape | GPL | opcional, capa e polimento |

## Testes

```bash
python3 -m unittest discover -s tests -v
```

São 17 testes, que rodam a CLI de verdade nos quatro tipos de figura e cobrem os casos de falha do `verify`. Se as bibliotecas científicas não
estiverem instaladas, os testes são pulados e o motivo aparece na saída.

## Licença

MIT. Veja [LICENSE](LICENSE).
