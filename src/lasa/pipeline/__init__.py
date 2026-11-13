"""Pipeline stages, in run order.

Each stage reads the previous artifact and writes its own, so a run resumes
from any completed stage. See ARCHITECTURE.md.
"""

from lasa.pipeline import load, similarity, attack_base, predict, verify, report

STAGES = [load, similarity, attack_base, predict, verify, report]
