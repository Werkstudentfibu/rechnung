"""Template-basierte Rechnungsextraktion (v1)."""

__version__ = "0.1.0"

# Bindet die ausdrueckliche Auswahl "mit bestehendem Kreditor zusammenfuehren"
# in den vorhandenen Excel-Importablauf ein.
from .stammdaten_import_merge import install as _install_stammdaten_import_merge

_install_stammdaten_import_merge()
del _install_stammdaten_import_merge
