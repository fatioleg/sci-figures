#!/usr/bin/env python3
"""figs — figuras científicas reproduzíveis a partir de especificações curtas.

Cada figura vive em figures/<id>/ com:
  fig.yaml          especificação (tipo, legenda, texto alternativo, saídas, estilo, fonte)
  <fonte nativa>    lista de SMILES no próprio fig.yaml, diagram.d2, geometria (xyz/cif/POSCAR/saída ORCA) ou plot.py
  <id>.svg|pdf|png  saídas geradas (nunca editar à mão)
  manifest.json     proveniência: hashes, versões das ferramentas, identificadores químicos, checagens

Tipos e motores (determinísticos; o LLM só escreve a especificação):
  molecule-2d  RDKit, estilo ACS 1996 (padrão de periódico), grade de moléculas com legendas
  diagram      D2 (layout elk), fluxogramas e pipelines
  structure    ASE + matplotlib: moléculas, aglomerados, células, superfícies, adsorção
  plot         matplotlib com estilo uah-pub (dados reais: energias, convergência, espectros)

Comandos: setup · doctor · new · render · verify · qmd
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

VERSION = "0.1.0"
SKILL = Path(__file__).resolve().parents[1]
VENV = Path(os.environ.get("SCI_FIGURES_VENV", Path.home() / ".local/share/sci-figures/venv"))
DEPS = ["rdkit", "ase", "matplotlib", "pyyaml", "pillow", "cairosvg"]
KINDS = ("molecule-2d", "diagram", "structure", "plot")
FORMATS = ("svg", "pdf", "png")
DEFAULT_SOURCE = {"diagram": "diagram.d2", "plot": "plot.py"}
DATA_KINDS = ("plot", "structure")


def _subcommand(argv: list[str]) -> str | None:
    """Primeiro argumento posicional (pula opções e o valor de --dir)."""
    skip = False
    for arg in argv:
        if skip:
            skip = False
        elif arg == "--dir":
            skip = True
        elif not arg.startswith("-"):
            return arg
    return None


def _reexec_in_venv() -> None:
    """Usa o venv dedicado quando as bibliotecas não estão no Python atual."""
    if os.environ.get("SCI_FIGURES_NO_REEXEC"):
        return
    try:
        for mod in ("yaml", "rdkit", "ase", "matplotlib", "cairosvg", "PIL"):
            __import__(mod)
        return
    except ImportError:
        pass
    py = VENV / "bin" / "python"
    in_venv = Path(sys.prefix).resolve() == VENV.resolve()  # o python do venv é link para o do sistema
    if py.is_file() and not in_venv and _subcommand(sys.argv[1:]) != "setup":
        os.execv(str(py), [str(py), *sys.argv])


_reexec_in_venv()

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


class FigError(Exception):
    pass


# ----------------------------------------------------------------- helpers

def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 300, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
                          encoding="utf-8", errors="replace")


def need_yaml() -> None:
    if yaml is None:
        raise FigError("PyYAML ausente: rode `figs.py setup` (cria o venv com as dependências)")


def load_spec(fig_dir: Path) -> dict:
    need_yaml()
    path = fig_dir / "fig.yaml"
    if not path.is_file():
        raise FigError(f"{path} não existe")
    try:
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise FigError(f"{path}: YAML inválido ({exc})") from exc
    if not isinstance(spec, dict):
        raise FigError(f"{path}: esperado mapeamento")
    spec.setdefault("id", fig_dir.name)
    spec.setdefault("outputs", list(FORMATS))
    spec.setdefault("dpi", 300)
    spec.setdefault("width_mm", 85)
    return spec


def validate_spec(fig_dir: Path, spec: dict) -> list[tuple[str, str]]:
    out = []
    if spec.get("id") != fig_dir.name:
        out.append(("FAIL", f"id {spec.get('id')!r} difere da pasta {fig_dir.name!r}"))
    if spec.get("kind") not in KINDS:
        out.append(("FAIL", f"kind deve ser um de {', '.join(KINDS)}"))
    for key in ("title", "alt"):
        if not str(spec.get(key) or "").strip():
            out.append(("FAIL", f"campo obrigatório vazio: {key}" + (" (texto alternativo para acessibilidade)" if key == "alt" else "")))
    for key in ("width_mm", "dpi"):
        value = spec.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            out.append(("FAIL", f"{key} deve ser um número positivo (recebido {value!r})"))
    if spec.get("kind") == "molecule-2d" and (not isinstance(spec.get("molecules"), list) or not spec["molecules"]):
        out.append(("FAIL", "molecules deve ser uma lista não vazia de {smiles, legend}"))
    outputs = spec.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        out.append(("FAIL", f"outputs deve ser uma lista não vazia de {', '.join(FORMATS)}"))
    elif [f for f in outputs if f not in FORMATS]:
        out.append(("FAIL", f"saídas inválidas {[f for f in outputs if f not in FORMATS]}; use {', '.join(FORMATS)}"))
    if spec.get("ai_generated"):
        if spec.get("kind") in DATA_KINDS or spec.get("data_figure"):
            out.append(("FAIL", "figura de dados não pode ser gerada por IA (ai_generated: true)"))
        else:
            out.append(("WARN", "figura conceitual gerada por IA: identifique isso na legenda"))
    return out


def figures_root(base: Path) -> Path:
    return base.resolve()


def select(base: Path, ids: list[str]) -> list[Path]:
    root = figures_root(base)
    if ids:
        dirs = [root / i for i in ids]
        missing = [d.name for d in dirs if not (d / "fig.yaml").is_file()]
        if missing:
            raise FigError(f"figura(s) inexistente(s): {', '.join(missing)}")
        return dirs
    dirs = sorted(p.parent for p in root.glob("*/fig.yaml"))
    if not dirs:
        raise FigError(f"nenhuma figura em {root} (confira --dir; crie com `figs.py new`)")
    return dirs


def target_px(spec: dict) -> int:
    return int(round(float(spec["width_mm"]) / 25.4 * int(spec["dpi"])))


def svg_to(fig_dir: Path, spec: dict, svg: Path) -> None:
    import cairosvg
    stem = fig_dir / spec["id"]
    if "pdf" in spec["outputs"]:
        cairosvg.svg2pdf(url=str(svg), write_to=str(stem.with_suffix(".pdf")))
    if "png" in spec["outputs"]:
        cairosvg.svg2png(url=str(svg), write_to=str(stem.with_suffix(".png")), output_width=target_px(spec),
                         background_color="white")


def tool_versions() -> dict:
    versions = {"figs": VERSION}
    for mod in ("rdkit", "ase", "matplotlib", "cairosvg"):
        try:
            versions[mod] = __import__(mod).__version__
        except Exception:  # noqa: BLE001
            versions[mod] = None
    d2 = shutil.which("d2")
    versions["d2"] = run([d2, "--version"]).stdout.strip() if d2 else None
    return versions


# --------------------------------------------------------------- renderers

def render_molecule_2d(fig_dir: Path, spec: dict) -> dict:
    from rdkit import Chem
    from rdkit.Chem import Draw, rdDepictor
    from rdkit.Chem.Draw import rdMolDraw2D
    from rdkit.Chem.rdMolDescriptors import CalcMolFormula

    entries = spec.get("molecules") or []
    if not entries:
        raise FigError("molecule-2d: liste `molecules: [{smiles: ..., legend: ...}]`")
    mols, info = [], []
    for i, entry in enumerate(entries, 1):
        smi = entry.get("smiles") if isinstance(entry, dict) else str(entry)
        mol = Chem.MolFromSmiles(smi or "")
        if mol is None:
            raise FigError(f"molecule-2d: SMILES inválido na posição {i}: {smi!r}")
        rdDepictor.SetPreferCoordGen(True)
        rdDepictor.Compute2DCoords(mol)
        mols.append(mol)
        info.append({"input": smi, "legend": (entry.get("legend") if isinstance(entry, dict) else None) or "",
                     "canonical_smiles": Chem.MolToSmiles(mol), "inchikey": Chem.MolToInchiKey(mol),
                     "formula": CalcMolFormula(mol)})
    highlight = spec.get("highlight_smarts")
    patt = Chem.MolFromSmarts(highlight) if highlight else None
    if highlight and patt is None:
        raise FigError(f"molecule-2d: SMARTS inválido em highlight_smarts: {highlight!r}")

    # Cada molécula em quadro próprio (modo ACS 1996: comprimento de ligação fixo, como nas revistas).
    pieces = []
    for mol, meta in zip(mols, info):
        drawer = rdMolDraw2D.MolDraw2DSVG(-1, -1)
        opts = drawer.drawOptions()
        rdMolDraw2D.SetACS1996Mode(opts, Draw.MeanBondLength(mol) or 1.5)
        opts.clearBackground = False
        atoms = list(mol.GetSubstructMatch(patt)) if patt is not None else []
        drawer.DrawMolecule(mol, highlightAtoms=atoms)
        drawer.FinishDrawing()
        svg = drawer.GetDrawingText()
        m = re.search(r"width='([\d.]+)px'\s+height='([\d.]+)px'", svg) or re.search(r'width="([\d.]+)"\s+height="([\d.]+)"', svg)
        w, h = (float(m.group(1)), float(m.group(2))) if m else (150.0, 150.0)
        body = re.sub(r"^.*?<svg[^>]*>", "", svg, count=1, flags=re.S).rsplit("</svg>", 1)[0]
        pieces.append((w, h, body, meta["legend"]))

    per_row = max(1, int(spec.get("per_row", 4)))
    gap, legend_h = 18.0, 16.0 if any(p[3] for p in pieces) else 0.0
    rows = [pieces[i:i + per_row] for i in range(0, len(pieces), per_row)]
    cell_w = max(p[0] for p in pieces)
    width = per_row if len(pieces) >= per_row else len(pieces)
    total_w = width * cell_w + (width - 1) * gap
    row_heights = [max(p[1] for p in row) + legend_h for row in rows]
    total_h = sum(row_heights) + (len(rows) - 1) * gap
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="{total_w:.1f}pt" height="{total_h:.1f}pt" '
             f'viewBox="0 0 {total_w:.1f} {total_h:.1f}">', f'<rect width="100%" height="100%" fill="white"/>']
    y = 0.0
    for row, rh in zip(rows, row_heights):
        for col, (w, h, body, legend) in enumerate(row):
            x = col * (cell_w + gap) + (cell_w - w) / 2
            parts.append(f'<svg x="{x:.1f}" y="{y + (rh - legend_h - h) / 2:.1f}" width="{w:.1f}" height="{h:.1f}" '
                         f'viewBox="0 0 {w:.1f} {h:.1f}">{body}</svg>')
            if legend:
                cx = col * (cell_w + gap) + cell_w / 2
                safe = legend.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                parts.append(f'<text x="{cx:.1f}" y="{y + rh - 4:.1f}" text-anchor="middle" '
                             f'font-family="Arial, Helvetica, sans-serif" font-size="10">{safe}</text>')
        y += rh + gap
    parts.append("</svg>")
    svg_path = fig_dir / f"{spec['id']}.svg"
    svg_path.write_text("\n".join(parts), encoding="utf-8")
    svg_to(fig_dir, spec, svg_path)
    return {"molecules": info}


D2_RESERVED = {"direction", "shape", "style", "label", "icon", "near", "width", "height", "vars", "classes",
               "class", "layout", "link", "tooltip", "constraint", "grid-rows", "grid-columns", "title"}


def _d2_strip_comment(line: str) -> str:
    """Remove o comentário `#` que estiver fora de aspas."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote and line[i - 1] != "\\":
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#":
            return line[:i]
    return line


def _d2_unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1].replace("\\" + text[0], text[0])
    return text


def d2_labels(source: str) -> list[str]:
    """Rótulos de formas (`chave: Rótulo`, `label: Rótulo`) e de conexões (`a -> b: Rótulo`).

    Ignora palavras reservadas do D2 (style, direction…) e blocos de markdown/código (`|md ...|`).
    """
    labels = []
    skip_depth = None  # dentro de style/vars/classes: chaves não são rótulos
    depth = 0
    for raw in source.splitlines():
        line = _d2_strip_comment(raw).strip().rstrip(";")
        bare = re.sub(r"""("[^"]*"|'[^']*')""", "", line)
        opens, closes = bare.count("{"), bare.count("}")
        if skip_depth is not None:
            depth += opens - closes
            if depth <= skip_depth:
                skip_depth = None
            continue
        key_part = bare.split(":", 1)[0]
        if opens > closes and key_part.strip().split(".")[-1] in {"style", "vars", "classes"}:
            skip_depth = depth
            depth += opens - closes
            continue
        depth += opens - closes
        if "${" in line:
            continue  # substituição de variável: o texto final só existe no SVG
        m = re.match(r"""^((?:"[^"]*"|'[^']*'|[^:"'])+?)\s*:\s*(.+?)\s*(\{.*)?$""", line)
        if not m:
            continue
        key, label = m.group(1).strip(), m.group(2).strip()
        last = re.split(r"\s*(?:<->|->|<-|--)\s*", key)[-1].split(".")[-1]
        is_edge = bool(re.search(r"<->|->|<-|--", key))
        if not is_edge and (last in D2_RESERVED - {"label"} or "style" in key.split(".")):
            continue
        if not label or label.startswith(("|", "{")):
            continue
        labels.append(_d2_unquote(label))
    return [l for l in labels if l]


def render_diagram(fig_dir: Path, spec: dict) -> dict:
    """D2 exporta SVG, PNG e PDF nativamente (o SVG do D2 usa CSS/fontes que o cairosvg não desenha)."""
    d2 = shutil.which("d2")
    if not d2:
        raise FigError("diagram: D2 não encontrado no PATH (veja `figs.py doctor`)")
    src = fig_dir / spec.get("source", DEFAULT_SOURCE["diagram"])
    if not src.is_file():
        raise FigError(f"diagram: fonte {src.name} não encontrada")
    base = [d2, "--layout", str(spec.get("layout", "elk")), "--theme", str(spec.get("theme", 0)),
            "--pad", str(spec.get("pad", 20))]
    if spec.get("sketch"):
        base.append("--sketch")
    stem = fig_dir / spec["id"]

    def d2_out(path: Path, scale: float) -> None:
        out = run([*base, "--scale", f"{scale:.4f}", str(src), str(path)], cwd=fig_dir)
        if out.returncode != 0 or not path.is_file():
            raise FigError(f"diagram: d2 falhou ({path.suffix}): {(out.stderr or out.stdout).strip()[:400]}")

    for fmt in spec["outputs"]:
        path = stem.with_suffix(f".{fmt}")
        if fmt == "png":
            from PIL import Image
            d2_out(path, 1.0)
            with Image.open(path) as im:
                width = im.width
            scale = target_px(spec) / width
            if abs(scale - 1) > 0.02:
                d2_out(path, scale)
        else:
            d2_out(path, 1.0)
    return {"labels": d2_labels(src.read_text(encoding="utf-8"))}


def draw_ball_stick(ax, atoms, rotation: str, radii: float, bond_width: float, labels: str | None) -> int:
    """Bola-e-bastão com ordenação por profundidade; ligações pelos raios covalentes (natural_cutoffs × 1,1)."""
    import numpy as np
    from matplotlib.patches import Circle
    from ase.data import covalent_radii
    from ase.data.colors import jmol_colors
    from ase.neighborlist import NeighborList, natural_cutoffs
    from ase.utils import rotate

    pos = atoms.positions @ rotate(rotation)
    nums = atoms.numbers
    nl = NeighborList(natural_cutoffs(atoms, mult=1.1), self_interaction=False, bothways=False, skin=0.0)
    nl.update(atoms)
    order = {i: r for r, i in enumerate(np.argsort(pos[:, 2]))}
    nbonds = 0
    for i in range(len(atoms)):
        idx, offsets = nl.get_neighbors(i)
        for j, off in zip(idx, offsets):
            if np.any(off):
                continue  # ligação através da borda periódica: não desenhar
            a, b = pos[i, :2], pos[j, :2]
            mid = (a + b) / 2
            z = (order[i] + order[j]) / 2 * 3 + 1
            for start, end, num in ((a, mid, nums[i]), (mid, b, nums[j])):
                ax.plot([start[0], end[0]], [start[1], end[1]], color=jmol_colors[num], lw=bond_width,
                        solid_capstyle="butt", zorder=z)
                ax.plot([start[0], end[0]], [start[1], end[1]], color="0.25", lw=bond_width + 0.8,
                        solid_capstyle="butt", zorder=z - 0.5)
            nbonds += 1
    for i in range(len(atoms)):
        r = covalent_radii[nums[i]] * radii
        ax.add_patch(Circle(pos[i, :2], r, facecolor=jmol_colors[nums[i]], edgecolor="0.25", lw=0.6,
                            zorder=order[i] * 3 + 2))
        if labels:
            text = str(i) if labels == "indices" else atoms[i].symbol
            ax.text(pos[i, 0], pos[i, 1], text, ha="center", va="center", fontsize=5, zorder=order[i] * 3 + 2.5)
    pad = covalent_radii[nums].max() * radii + 0.3
    ax.set_xlim(pos[:, 0].min() - pad, pos[:, 0].max() + pad)
    ax.set_ylim(pos[:, 1].min() - pad, pos[:, 1].max() + pad)
    ax.set_aspect("equal")
    return nbonds


def render_structure(fig_dir: Path, spec: dict) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ase.io import read
    from ase.visualize.plot import plot_atoms

    src = fig_dir / str(spec.get("source", ""))
    if not spec.get("source") or not src.is_file():
        raise FigError("structure: informe `source:` com um arquivo de geometria existente (xyz, cif, POSCAR, saída ORCA…)")
    kwargs = {"format": spec["format"]} if spec.get("format") else {}
    atoms = read(str(src), index=spec.get("index", -1), **kwargs)
    rep = spec.get("repeat")
    if rep:
        atoms = atoms.repeat(tuple(rep))
    plt.style.use(str(SKILL / "styles" / "uah-pub.mplstyle"))
    plt.rcParams["savefig.bbox"] = "standard"  # sem recorte: a figura sai com width_mm exatos
    width_in = float(spec["width_mm"]) / 25.4
    fig, ax = plt.subplots(figsize=(width_in, width_in * float(spec.get("aspect", 0.75))))
    style = spec.get("style") or ("spacefill" if any(atoms.pbc) else "ball-stick")
    if style not in ("ball-stick", "spacefill"):
        raise FigError("structure: style deve ser ball-stick ou spacefill")
    nbonds = None
    if style == "ball-stick":
        nbonds = draw_ball_stick(ax, atoms, str(spec.get("rotation", "0x,0y,0z")), float(spec.get("radii", 0.35)),
                                 float(spec.get("bond_width", 2.2)), spec.get("labels"))
    else:
        plot_atoms(atoms, ax, rotation=str(spec.get("rotation", "0x,0y,0z")), radii=float(spec.get("radii", 0.9)),
                   show_unit_cell=2 if spec.get("show_cell") else 0)
    ax.set_axis_off()
    ax.set_position((0.02, 0.02, 0.96, 0.96))  # sem recorte automático: largura final = width_mm exatos
    stem = fig_dir / spec["id"]
    for fmt in spec["outputs"]:
        fig.savefig(stem.with_suffix(f".{fmt}"), dpi=int(spec["dpi"]), facecolor="white")
    plt.close(fig)
    return {"structure": {"formula": atoms.get_chemical_formula(), "natoms": len(atoms), "style": style, "bonds": nbonds,
                          "pbc": [bool(x) for x in atoms.pbc], "source_frame": spec.get("index", -1)}}


def render_plot(fig_dir: Path, spec: dict) -> dict:
    src = fig_dir / spec.get("source", DEFAULT_SOURCE["plot"])
    if not src.is_file():
        raise FigError(f"plot: script {src.name} não encontrado")
    env = dict(os.environ, FIGS_STYLE=str(SKILL / "styles" / "uah-pub.mplstyle"), FIGS_OUT=str(fig_dir / spec["id"]),
               FIGS_FORMATS=",".join(spec["outputs"]), FIGS_DPI=str(spec["dpi"]), FIGS_WIDTH_MM=str(spec["width_mm"]),
               MPLBACKEND="Agg")
    helper = (
        "import os, runpy, matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.style.use(os.environ['FIGS_STYLE'])\n"
        "w = float(os.environ['FIGS_WIDTH_MM'])/25.4\n"
        "plt.rcParams['figure.figsize'] = (w, w*0.75)\n"
        "plt.rcParams['figure.constrained_layout.use'] = True\n"
        "plt.rcParams['savefig.bbox'] = 'standard'  # sem recorte: largura final = width_mm exatos\n"
        "def save(fig=None):\n"
        "    fig = fig or plt.gcf()\n"
        "    for f in os.environ['FIGS_FORMATS'].split(','):\n"
        "        fig.savefig(os.environ['FIGS_OUT'] + '.' + f, dpi=int(os.environ['FIGS_DPI']), facecolor='white')\n"
        "g = runpy.run_path(%r, init_globals={'save': save, 'plt': plt}, run_name='__main__')\n"
        "if not any(os.path.exists(os.environ['FIGS_OUT'] + '.' + f) for f in os.environ['FIGS_FORMATS'].split(',')):\n"
        "    save()\n"
    ) % str(src)
    for f in spec["outputs"]:
        (fig_dir / f"{spec['id']}.{f}").unlink(missing_ok=True)
    out = run([sys.executable, "-c", helper], cwd=fig_dir, timeout=600, env=env)
    if out.returncode != 0:
        for f in spec["outputs"]:
            (fig_dir / f"{spec['id']}.{f}").unlink(missing_ok=True)  # nada de saída parcial que pareça atual
        raise FigError(f"plot: {src.name} falhou:\n" + "\n".join((out.stderr or out.stdout).strip().splitlines()[-12:]))
    return {}


RENDERERS = {"molecule-2d": render_molecule_2d, "diagram": render_diagram, "structure": render_structure,
             "plot": render_plot}


# ----------------------------------------------------------------- commands

def sources_of(fig_dir: Path, spec: dict) -> list[Path]:
    paths = [fig_dir / "fig.yaml"]
    source = spec.get("source") or DEFAULT_SOURCE.get(spec.get("kind"))
    if source:
        paths.append(fig_dir / str(source))
    paths += [fig_dir / str(p) for p in spec.get("inputs", [])]
    return paths


def source_hashes(fig_dir: Path, spec: dict) -> dict:
    return {str(s.relative_to(fig_dir)): sha256(s) for s in sources_of(fig_dir, spec) if s.is_file()}


def cmd_render(base: Path, ids: list[str]) -> int:
    status = 0
    for fig_dir in select(base, ids):
        spec = load_spec(fig_dir)
        problems = [p for p in validate_spec(fig_dir, spec) if p[0] == "FAIL"]
        if problems:
            print(f"render {fig_dir.name}: BLOQUEADO — " + "; ".join(m for _, m in problems))
            status = 1
            continue
        (fig_dir / ".render.json").unlink(missing_ok=True)
        try:
            hashes = source_hashes(fig_dir, spec)
            extra = RENDERERS[spec["kind"]](fig_dir, spec)
        except Exception as exc:  # noqa: BLE001 — erro de uma figura não derruba as outras
            msg = str(exc) if isinstance(exc, FigError) else f"{type(exc).__name__}: {exc}"
            print(f"render {fig_dir.name}: FALHOU — {msg}")
            status = 1
            continue
        missing = [f for f in spec["outputs"] if not (fig_dir / f"{spec['id']}.{f}").is_file()]
        if missing:
            print(f"render {fig_dir.name}: FALHOU — saídas não geradas: {', '.join(missing)}")
            status = 1
            continue
        produced = {f"{spec['id']}.{f}": sha256(fig_dir / f"{spec['id']}.{f}") for f in spec["outputs"]}
        (fig_dir / ".render.json").write_text(json.dumps({"sources": hashes, "rendered": produced, **extra}, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
        print(f"render {fig_dir.name}: OK ({', '.join(spec['outputs'])})")
    return status


def cmd_verify(base: Path, ids: list[str]) -> int:
    results: list[tuple[str, str]] = []
    review = figures_root(base) / "_review"
    thumbs = []
    versions = tool_versions()
    for fig_dir in select(base, ids):
        name = fig_dir.name
        fig_results: list[tuple[str, str]] = []
        add = lambda level, msg: fig_results.append((level, msg))  # noqa: E731
        spec = load_spec(fig_dir)
        spec_problems = validate_spec(fig_dir, spec)
        for level, msg in spec_problems:
            add(level, msg)
        kind = spec.get("kind")
        outputs_list = spec["outputs"] if isinstance(spec.get("outputs"), list) else []
        srcs = sources_of(fig_dir, spec)
        for s in srcs:
            if not s.is_file():
                add("FAIL", f"fonte declarada ausente: {s.name}")
        data_fig = bool(spec.get("data_figure", kind in DATA_KINDS))
        if data_fig and kind == "plot" and not spec.get("inputs"):
            add("FAIL", "figura de dados sem arquivo de dados declarado em `inputs:` (proveniência incompleta)")
        extra = {}
        if (fig_dir / ".render.json").is_file():
            extra = json.loads((fig_dir / ".render.json").read_text(encoding="utf-8"))
        rendered_from = extra.pop("sources", None)
        rendered_outputs = extra.pop("rendered", {})
        if rendered_from is None:
            add("FAIL", "sem registro de render (rode render)")
        elif rendered_from != source_hashes(fig_dir, spec):
            add("FAIL", "saídas desatualizadas: as fontes mudaram desde o último render (rode render)")
        outputs = {}
        for fmt in outputs_list:
            out = fig_dir / f"{spec['id']}.{fmt}"
            if not out.is_file() or out.stat().st_size == 0:
                add("FAIL", f"saída ausente: {out.name} (rode render)")
                continue
            outputs[out.name] = sha256(out)
            if rendered_outputs.get(out.name) != outputs[out.name]:
                add("FAIL", f"{out.name} difere do que o render produziu (editada à mão?); rode render")
        png = fig_dir / f"{spec['id']}.png"
        sizes_ok = not any(lv == "FAIL" and m.startswith(("width_mm", "dpi")) for lv, m in spec_problems)
        if "png" in outputs_list and png.is_file() and sizes_ok:
            from PIL import Image
            with Image.open(png) as im:
                need = int(target_px(spec) * 0.95)
                if im.width < need:
                    add("FAIL", f"PNG com {im.width}px de largura; mínimo {need}px para {spec['width_mm']} mm a {spec['dpi']} dpi")
                thumb = im.convert("RGB")
                thumb.thumbnail((420, 320))
                thumbs.append((name, thumb))
        svg = fig_dir / f"{spec['id']}.svg"
        if kind == "molecule-2d":
            from rdkit import Chem
            for entry in spec.get("molecules") or []:
                smi = entry.get("smiles") if isinstance(entry, dict) else str(entry)
                if Chem.MolFromSmiles(smi or "") is None:
                    add("FAIL", f"SMILES inválido: {smi!r}")
                if isinstance(entry, dict) and not entry.get("legend") and len(spec.get("molecules", [])) > 1:
                    add("WARN", f"molécula sem legenda: {smi}")
            for m in extra.get("molecules", []):
                add("INFO", f"{m['legend'] or m['input']}: {m['formula']} · InChIKey {m['inchikey']}")
        if kind == "diagram" and svg.is_file():
            text = svg.read_text(encoding="utf-8")
            plain = re.sub(r"<[^>]+>", " ", text)
            plain = html.unescape(plain)
            missing = [l for l in extra.get("labels", []) if l not in plain]
            if missing:
                add("FAIL", f"rótulos não encontrados como texto no SVG (truncados ou quebrados?): {missing[:5]}")
        if kind == "structure" and extra.get("structure"):
            st = extra["structure"]
            add("INFO", f"estrutura {st['formula']} ({st['natoms']} átomos, pbc={st['pbc']})")
        manifest = {"figs_version": VERSION, "verified_at": now_iso(), "id": spec.get("id"), "kind": kind,
                    "title": spec.get("title"), "alt": spec.get("alt"), "data_figure": data_fig,
                    "ai_generated": bool(spec.get("ai_generated", False)), "tools": versions,
                    "sources": source_hashes(fig_dir, spec), "outputs": outputs, **extra,
                    "checks": [{"level": lv, "message": m} for lv, m in fig_results],
                    "passed": not any(lv == "FAIL" for lv, _ in fig_results)}
        (fig_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        results += [(lv, f"{name}: {m}") for lv, m in fig_results]
    review.mkdir(parents=True, exist_ok=True)
    (review / "contato.png").unlink(missing_ok=True)  # nunca deixar uma folha antiga passar por atual
    if thumbs:
        from PIL import Image, ImageDraw
        per_row = min(4, len(thumbs))
        rows = (len(thumbs) + per_row - 1) // per_row
        cw, ch = 440, 360
        sheet = Image.new("RGB", (per_row * cw, rows * ch), (238, 238, 238))
        draw = ImageDraw.Draw(sheet)
        for i, (name, im) in enumerate(thumbs):
            x, y = (i % per_row) * cw, (i // per_row) * ch
            sheet.paste(im, (x + (cw - im.width) // 2, y + 10))
            draw.text((x + 10, y + ch - 24), name, fill=(40, 40, 40))
        sheet.save(review / "contato.png")
        results.append(("REVIEW", f"inspeção visual: abra {review / 'contato.png'}"))
    for level, msg in results:
        print(f"[{level}] {msg}")
    return 1 if any(level == "FAIL" for level, _ in results) else 0


TEMPLATES = {
    "molecule-2d": {
        "spec": {"kind": "molecule-2d", "title": "Estruturas dos compostos estudados.",
                 "alt": "Estruturas químicas 2D de benzeno e ácido acetilsalicílico.", "data_figure": False,
                 "outputs": ["svg", "pdf", "png"], "dpi": 300, "width_mm": 85, "per_row": 4,
                 "molecules": [{"smiles": "c1ccccc1", "legend": "benzeno"},
                               {"smiles": "CC(=O)Oc1ccccc1C(=O)O", "legend": "AAS"}]},
    },
    "diagram": {
        "spec": {"kind": "diagram", "title": "Fluxo de trabalho computacional.",
                 "alt": "Fluxograma: preparação, otimização, frequências e análise.", "data_figure": False,
                 "outputs": ["svg", "pdf", "png"], "dpi": 300, "width_mm": 170, "source": "diagram.d2",
                 "layout": "elk", "theme": 0},
        "files": {"diagram.d2": "direction: right\n\nprep: Preparação das estruturas\nopt: Otimização (DFT)\n"
                                "freq: Frequências\nana: Análise e figuras\n\nprep -> opt -> freq -> ana\n"},
    },
    "structure": {
        "spec": {"kind": "structure", "title": "Geometria otimizada.", "alt": "Estrutura 3D projetada da molécula de água.",
                 "data_figure": True, "outputs": ["svg", "pdf", "png"], "dpi": 300, "width_mm": 60,
                 "source": "geometria.xyz", "index": -1, "rotation": "0x,90y,0z", "style": "ball-stick", "show_cell": False},
        "files": {"geometria.xyz": "3\nágua (exemplo — substitua pela geometria real)\nO 0.000 0.000 0.117\n"
                                   "H 0.000 0.757 -0.469\nH 0.000 -0.757 -0.469\n"},
    },
    "plot": {
        "spec": {"kind": "plot", "title": "Perfil de energia relativa.", "alt": "Gráfico de energia relativa por etapa.",
                 "data_figure": True, "outputs": ["svg", "pdf", "png"], "dpi": 300, "width_mm": 85,
                 "source": "plot.py", "inputs": ["dados.csv"]},
        "files": {
            "dados.csv": "etapa,energia_kcal\nR,0.0\nTS,18.4\nI,-3.2\nP,-12.7\n",
            "plot.py": ("import csv\n"
                        "rows = list(csv.DictReader(open('dados.csv')))\n"
                        "x = range(len(rows)); y = [float(r['energia_kcal']) for r in rows]\n"
                        "fig, ax = plt.subplots()\n"
                        "ax.plot(list(x), y, marker='o')\n"
                        "ax.set_xticks(list(x), [r['etapa'] for r in rows])\n"
                        "ax.set_ylabel(r'$\\Delta E$ (kcal mol$^{-1}$)')\n"
                        "save(fig)\n"),
        },
    },
}


def cmd_new(base: Path, fig_id: str, kind: str, force: bool) -> None:
    need_yaml()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", fig_id):
        raise FigError("id deve ter só letras minúsculas, números e hífens (vira #fig-<id> no Quarto)")
    fig_dir = figures_root(base) / fig_id
    if (fig_dir / "fig.yaml").exists() and not force:
        raise FigError(f"{fig_dir}/fig.yaml já existe")
    fig_dir.mkdir(parents=True, exist_ok=True)
    tpl = TEMPLATES[kind]
    spec = {"id": fig_id, **tpl["spec"]}
    (fig_dir / "fig.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
    for name, content in tpl.get("files", {}).items():
        if not (fig_dir / name).exists():
            (fig_dir / name).write_text(content, encoding="utf-8")
    print(f"new: {fig_dir} ({kind}). Edite fig.yaml/fonte, depois `figs.py render {fig_id}` e `figs.py verify {fig_id}`.")


def cmd_qmd(base: Path, fig_id: str, rel_to: Path | None) -> None:
    fig_dir = figures_root(base) / fig_id
    spec = load_spec(fig_dir)
    svg = fig_dir / f"{spec['id']}.svg"
    path = os.path.relpath(svg, rel_to.resolve()) if rel_to else svg.as_posix()
    alt = str(spec.get("alt", "")).replace('"', "'")
    width = f" width={min(100, round(float(spec['width_mm']) / 170 * 100))}%"
    print(f'![{spec.get("title", "")}]({path}){{#fig-{spec["id"]} fig-alt="{alt}"{width}}}')


def cmd_doctor() -> int:
    rows = []
    for mod in ("yaml", "rdkit", "ase", "matplotlib", "cairosvg", "PIL"):
        try:
            m = __import__(mod)
            rows.append(("OK", mod, getattr(m, "__version__", "presente")))
        except ImportError:
            rows.append(("FAIL", mod, "ausente — rode `figs.py setup`"))
    d2 = shutil.which("d2")
    if d2:
        ver = run([d2, "--version"]).stdout.strip()
        m = re.search(r"(\d+)\.(\d+)", ver)
        old = not m or (int(m.group(1)), int(m.group(2))) < (0, 6)
        rows.append(("WARN" if old else "OK", "d2", ver + (" — precisa ≥ 0.6 (exportação PNG/PDF sem navegador)" if old else "")))
    else:
        rows.append(("WARN", "d2", "ausente — diagramas indisponíveis (https://d2lang.com/tour/install)"))
    for tool, why in (("pymol", "3D premium/biomoléculas por script (opcional)"), ("vesta", "cristais/isossuperfícies, manual (opcional)"),
                      ("povray", "renderização ray-traced de estruturas ASE (opcional)")):
        rows.append(("OK" if shutil.which(tool) else "INFO", tool, "presente" if shutil.which(tool) else f"ausente — {why}"))
    rows.append(("INFO", "python", f"{sys.executable} (venv: {VENV})"))
    for level, name, msg in rows:
        print(f"[{level}] {name}: {msg}")
    return 1 if any(level == "FAIL" for level, _, _ in rows) else 0


def cmd_setup() -> int:
    if not (VENV / "bin" / "python").is_file():
        out = run([sys.executable, "-m", "venv", str(VENV)])
        if out.returncode != 0:
            raise FigError(f"falha ao criar venv em {VENV}: {out.stderr.strip()[:300]}")
    out = run([str(VENV / "bin" / "python"), "-m", "pip", "install", "-q", *DEPS], timeout=1800)
    if out.returncode != 0:
        raise FigError(f"pip falhou: {(out.stderr or out.stdout).strip()[-400:]}")
    print(f"setup: venv pronto em {VENV} com {', '.join(DEPS)}")
    if not shutil.which("d2"):
        print("Aviso: D2 não está no PATH. Instale-o para diagramas: https://d2lang.com/tour/install")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="figs.py", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"figs {VERSION}")
    parser.add_argument("--dir", default="figures", help="pasta das figuras (padrão: figures)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="cria o venv com RDKit, ASE, matplotlib, cairosvg")
    sub.add_parser("doctor", help="verifica as ferramentas")
    p = sub.add_parser("new", help="cria figures/<id>/ a partir de um modelo")
    p.add_argument("id")
    p.add_argument("--kind", choices=KINDS, required=True)
    p.add_argument("--force", action="store_true")
    for name in ("render", "verify"):
        sp = sub.add_parser(name)
        sp.add_argument("ids", nargs="*")
    p = sub.add_parser("qmd", help="imprime o trecho Quarto para incluir a figura")
    p.add_argument("id")
    p.add_argument("--relative-to", help="pasta do document.qmd (para o caminho relativo)")
    args = parser.parse_args(argv)
    base = Path(args.dir)
    try:
        if args.cmd == "setup":
            return cmd_setup()
        if args.cmd == "doctor":
            return cmd_doctor()
        if args.cmd == "new":
            cmd_new(base, args.id, args.kind, args.force)
            return 0
        if args.cmd == "render":
            return cmd_render(base, args.ids)
        if args.cmd == "verify":
            return cmd_verify(base, args.ids)
        if args.cmd == "qmd":
            cmd_qmd(base, args.id, Path(args.relative_to) if args.relative_to else None)
            return 0
    except FigError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
