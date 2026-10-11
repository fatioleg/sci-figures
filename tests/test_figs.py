"""Testes do figs (sci-figures).

Rodam a CLI de verdade em pastas temporárias. A CLI usa o venv dedicado
(~/.local/share/sci-figures/venv) automaticamente; sem as bibliotecas científicas
os testes de renderização são pulados com o motivo explícito (dependência de ambiente).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

FIGS = Path(__file__).resolve().parents[1] / "sci-figures" / "scripts" / "figs.py"
VENV_PY = Path(os.environ.get("SCI_FIGURES_VENV", Path.home() / ".local/share/sci-figures/venv")) / "bin" / "python"


def has_stack() -> bool:
    py = str(VENV_PY) if VENV_PY.is_file() else sys.executable
    return subprocess.run([py, "-c", "import rdkit, ase, matplotlib, cairosvg, yaml, PIL"],
                          capture_output=True).returncode == 0


STACK = has_stack()


def figs(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(FIGS), *args], cwd=cwd, capture_output=True, text=True, timeout=600)


@unittest.skipUnless(STACK, "RDKit/ASE/matplotlib/cairosvg indisponíveis (rode figs.py setup)")
class FigsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def spec(self, fig_id: str) -> Path:
        return self.root / "figures" / fig_id / "fig.yaml"

    def edit(self, fig_id: str, old: str, new: str) -> None:
        path = self.spec(fig_id)
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def new(self, fig_id: str, kind: str) -> None:
        out = figs("new", fig_id, "--kind", kind, cwd=self.root)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_molecule_2d_render_verify_and_manifest(self):
        self.new("mols", "molecule-2d")
        self.assertEqual(figs("render", "mols", cwd=self.root).returncode, 0)
        out = figs("verify", "mols", cwd=self.root)
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("UHOVQNZJYSORNB-UHFFFAOYSA-N", out.stdout)  # InChIKey do benzeno
        manifest = json.loads((self.root / "figures/mols/manifest.json").read_text())
        self.assertEqual(set(manifest["outputs"]), {"mols.svg", "mols.pdf", "mols.png"})
        self.assertEqual(manifest["molecules"][1]["formula"], "C9H8O4")
        self.assertTrue(manifest["tools"]["rdkit"])
        self.assertTrue((self.root / "figures/_review/contato.png").is_file())

    def test_invalid_smiles_blocks_render(self):
        self.new("ruim", "molecule-2d")
        self.edit("ruim", "smiles: c1ccccc1", "smiles: c1ccccc1XX")
        out = figs("render", "ruim", cwd=self.root)
        self.assertEqual(out.returncode, 1)
        self.assertIn("SMILES inválido", out.stdout)

    def test_structure_ball_stick_and_periodic_spacefill(self):
        self.new("agua", "structure")
        self.assertEqual(figs("render", "agua", cwd=self.root).returncode, 0)
        out = figs("verify", "agua", cwd=self.root)
        self.assertEqual(out.returncode, 0, out.stdout)
        manifest = json.loads((self.root / "figures/agua/manifest.json").read_text())
        self.assertEqual(manifest["structure"]["style"], "ball-stick")
        self.assertEqual(manifest["structure"]["bonds"], 2)
        cif = self.root / "figures/nacl"
        cif.mkdir(parents=True)
        subprocess.run([str(VENV_PY) if VENV_PY.is_file() else sys.executable, "-c",
                        "from ase.build import bulk; from ase.io import write; "
                        f"write(r'{cif}/nacl.cif', bulk('NaCl','rocksalt',a=5.64))"], check=True)
        (cif / "fig.yaml").write_text("id: nacl\nkind: structure\ntitle: NaCl.\nalt: Célula do NaCl.\n"
                                      "source: nacl.cif\nrepeat: [2, 2, 2]\nshow_cell: true\nwidth_mm: 60\n")
        self.assertEqual(figs("render", "nacl", cwd=self.root).returncode, 0)
        figs("verify", "nacl", cwd=self.root)
        manifest = json.loads((cif / "manifest.json").read_text())
        self.assertEqual(manifest["structure"]["style"], "spacefill")
        self.assertEqual(manifest["structure"]["natoms"], 16)

    def test_plot_runs_script_with_style_and_requires_inputs_for_provenance(self):
        self.new("perfil", "plot")
        render = figs("render", "perfil", cwd=self.root)
        self.assertEqual(render.returncode, 0, render.stdout)
        out = figs("verify", "perfil", cwd=self.root)
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertNotIn("sem arquivo de dados", out.stdout)
        self.assertNotIn("PNG com", out.stdout)  # largura exata de width_mm, sem recorte tight
        self.edit("perfil", "inputs:\n- dados.csv\n", "")
        out = figs("verify", "perfil", cwd=self.root)
        self.assertEqual(out.returncode, 1)
        self.assertIn("[FAIL] perfil: figura de dados sem arquivo de dados declarado", out.stdout)

    def test_ai_generated_data_figure_is_refused(self):
        self.new("perfil", "plot")
        self.edit("perfil", "data_figure: true", "data_figure: true\nai_generated: true")
        out = figs("render", "perfil", cwd=self.root)
        self.assertEqual(out.returncode, 1)
        self.assertIn("não pode ser gerada por IA", out.stdout)

    def test_missing_alt_and_stale_outputs_fail(self):
        self.new("mols", "molecule-2d")
        figs("render", "mols", cwd=self.root)
        self.edit("mols", "legend: AAS", "legend: aspirina")
        stamp = (self.root / "figures/mols/mols.svg").stat().st_mtime - 10
        os.utime(self.root / "figures/mols/mols.svg", (stamp, stamp))
        self.assertIn("desatualizada", figs("verify", "mols", cwd=self.root).stdout)
        self.edit("mols", "alt: Estruturas", "alt: ''\nx_old_alt: Estruturas")
        out = figs("render", "mols", cwd=self.root)
        self.assertIn("campo obrigatório vazio: alt", out.stdout)

    def test_edit_of_default_source_is_detected_by_hash(self):
        self.new("perfil", "plot")
        self.edit("perfil", "source: plot.py\n", "")  # fonte implícita (plot.py)
        self.assertEqual(figs("render", "perfil", cwd=self.root).returncode, 0)
        script = self.root / "figures/perfil/plot.py"
        script.write_text(script.read_text().replace("marker='o'", "marker='s'"))  # sem esperar 1 s
        out = figs("verify", "perfil", cwd=self.root)
        self.assertEqual(out.returncode, 1)
        self.assertIn("as fontes mudaram desde o último render", out.stdout)
        manifest = json.loads((self.root / "figures/perfil/manifest.json").read_text())
        self.assertIn("plot.py", manifest["sources"])
        self.assertFalse(manifest["passed"])

    def test_nothing_to_verify_and_empty_outputs_fail(self):
        self.assertEqual(figs("verify", cwd=self.root).returncode, 3)
        self.new("mols", "molecule-2d")
        self.edit("mols", "outputs:\n- svg\n- pdf\n- png\n", "outputs: []\n")
        out = figs("render", "mols", cwd=self.root)
        self.assertEqual(out.returncode, 1)
        self.assertIn("outputs deve ser uma lista não vazia", out.stdout)

    def test_one_broken_figure_does_not_stop_the_batch(self):
        self.new("a-st", "structure")
        (self.root / "figures/a-st/geometria.xyz").write_text("isto não é xyz\n")
        self.new("b-mol", "molecule-2d")
        out = figs("render", cwd=self.root)
        self.assertEqual(out.returncode, 1)
        self.assertIn("render a-st: FALHOU", out.stdout)
        self.assertIn("render b-mol: OK", out.stdout)
        self.assertNotIn("Traceback", out.stderr)

    def test_missing_kind_reports_fail_without_crash(self):
        self.new("mols", "molecule-2d")
        self.edit("mols", "kind: molecule-2d\n", "")
        out = figs("verify", "mols", cwd=self.root)
        self.assertEqual(out.returncode, 1)
        self.assertIn("kind deve ser um de", out.stdout)
        self.assertNotIn("Traceback", out.stderr)

    def test_plot_that_crashes_after_save_leaves_no_outputs(self):
        self.new("perfil", "plot")
        script = self.root / "figures/perfil/plot.py"
        script.write_text(script.read_text() + "raise RuntimeError('quebrou depois de salvar')\n")
        out = figs("render", "perfil", cwd=self.root)
        self.assertEqual(out.returncode, 1)
        self.assertFalse((self.root / "figures/perfil/perfil.png").exists())
        self.assertEqual(figs("verify", "perfil", cwd=self.root).returncode, 1)

    def test_ai_generated_plot_refused_even_if_marked_conceptual(self):
        self.new("perfil", "plot")
        self.edit("perfil", "data_figure: true", "data_figure: false\nai_generated: true")
        self.assertIn("não pode ser gerada por IA", figs("render", "perfil", cwd=self.root).stdout)

    def test_hand_edited_output_and_bad_numbers_fail(self):
        self.new("mols", "molecule-2d")
        figs("render", "mols", cwd=self.root)
        (self.root / "figures/mols/mols.svg").write_text("<svg/>")
        self.assertIn("mols.svg difere do que o render produziu", figs("verify", "mols", cwd=self.root).stdout)
        self.edit("mols", "width_mm: 85", "width_mm: abc")
        out = figs("verify", "mols", cwd=self.root)
        self.assertIn("width_mm deve ser um número positivo", out.stdout)
        self.assertNotIn("Traceback", out.stderr)

    def test_figure_named_setup_still_uses_the_venv(self):
        self.new("setup", "molecule-2d")
        out = figs("render", "setup", cwd=self.root)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    @unittest.skipUnless(shutil.which("d2"), "D2 não instalado neste ambiente")
    def test_diagram_label_check_ignores_style_vars_and_decodes_entities(self):
        self.new("fluxo", "diagram")
        (self.root / "figures/fluxo/diagram.d2").write_text(
            "vars: {\n  nome: Gama\n}\n"
            "a: Alpha {\n  style: {\n    fill: \"#ffcccc\"\n    stroke-dash: 3\n  }\n}\n"
            "b: \"Rock 'n' roll\"\nc: Tom & Jerry \"x\" <y>\nd: ${nome}\n"
            "a -> b: \"passo #1\"\nb -> c -> d\n")
        self.assertEqual(figs("render", "fluxo", cwd=self.root).returncode, 0)
        out = figs("verify", "fluxo", cwd=self.root)
        self.assertEqual(out.returncode, 0, out.stdout)
        labels = json.loads((self.root / "figures/fluxo/manifest.json").read_text())["labels"]
        self.assertEqual(labels, ["Alpha", "Rock 'n' roll", 'Tom & Jerry "x" <y>', "passo #1"])

    @unittest.skipUnless(shutil.which("d2"), "D2 não instalado neste ambiente")
    def test_diagram_native_exports_and_label_check(self):
        self.new("fluxo", "diagram")
        self.assertEqual(figs("render", "fluxo", cwd=self.root).returncode, 0)
        out = figs("verify", "fluxo", cwd=self.root)
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertNotIn("rótulos não encontrados", out.stdout)
        manifest = json.loads((self.root / "figures/fluxo/manifest.json").read_text())
        self.assertIn("Otimização (DFT)", manifest["labels"])
        from_png = subprocess.run([str(VENV_PY) if VENV_PY.is_file() else sys.executable, "-c",
                                   f"from PIL import Image; print(Image.open(r'{self.root}/figures/fluxo/fluxo.png').width)"],
                                  capture_output=True, text=True)
        self.assertGreaterEqual(int(from_png.stdout), int(170 / 25.4 * 300 * 0.95))

    def test_qmd_snippet_and_id_rules(self):
        self.new("mols", "molecule-2d")
        out = figs("qmd", "mols", "--relative-to", "publication", cwd=self.root)
        self.assertIn("](../figures/mols/mols.svg){#fig-mols fig-alt=", out.stdout)
        self.assertEqual(figs("new", "Ruim_ID", "--kind", "plot", cwd=self.root).returncode, 3)


@unittest.skipUnless(STACK, "RDKit/ASE/matplotlib/cairosvg indisponíveis (rode figs.py setup)")
class CodexAuditFigs(unittest.TestCase):
    """Auditoria do Codex: proveniência da trajetória em molecule-3d e saída correta no qmd."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fig = self.root / "figures" / "md"
        self.fig.mkdir(parents=True)
        (self.fig / "proteina.pdb").write_text("ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\nEND\n")
        (self.fig / "traj.xtc").write_bytes(b"\x00" * 64)
        (self.fig / "fig.yaml").write_text("id: md\nkind: molecule-3d\ntitle: Proteína.\nalt: Proteína em cartoon.\n"
                                           "data_figure: true\nsource: proteina.pdb\ntrajectory: traj.xtc\n"
                                           "outputs: [png]\nwidth_mm: 85\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_trajectory_is_a_tracked_source(self):
        png = self.fig / "md.png"
        png.write_bytes(b"\x89PNG")
        self.assertIn("md.png ilegível", figs("verify", "md", cwd=self.root).stdout)  # PNG falso: reprova, não quebra
        manifest = json.loads((self.fig / "manifest.json").read_text())
        self.assertIn("traj.xtc", manifest["sources"])
        self.assertIn("molar_vis", manifest["tools"])
        # registro de render feito com a trajetória atual → depois a trajetória muda
        import hashlib
        record = {"sources": manifest["sources"], "rendered": {"md.png": hashlib.sha256(png.read_bytes()).hexdigest()}}
        (self.fig / ".render.json").write_text(json.dumps(record))
        self.assertNotIn("desatualizadas", figs("verify", "md", cwd=self.root).stdout)
        (self.fig / "traj.xtc").write_bytes(b"\x01" * 64)
        self.assertIn("saídas desatualizadas", figs("verify", "md", cwd=self.root).stdout)
        (self.fig / "traj.xtc").unlink()
        self.assertIn("fonte declarada ausente: traj.xtc", figs("verify", "md", cwd=self.root).stdout)

    def test_qmd_points_to_png_for_molecule_3d(self):
        out = figs("qmd", "md", "--relative-to", "publication", cwd=self.root)
        self.assertIn("](../figures/md/md.png){#fig-md", out.stdout)
        self.assertNotIn(".svg", out.stdout)


if __name__ == "__main__":
    unittest.main()
