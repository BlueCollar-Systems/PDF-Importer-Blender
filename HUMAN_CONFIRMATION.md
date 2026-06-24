# Human Confirmation — PDF Vector Importer (Blender)

**Coordination:** see Desktop Q&A COORDINATION-HUB or `_LLM_CONTROL_PACK/QA/QA-2026-06-24_COORDINATION-HUB.md`

**Prep:** 2026-06-24 · See `Desktop/PDFTest Files/Q&A/QA-2026-06-24_human-confirmation-script.md`

## Setup

1. Add-on **v1.0.42+**.
2. `$env:BCS_CORPUS_ROOT = 'C:\1pdf-test-corpus'`
3. `python C:\1pdf-test-corpus\tools\list_tier1.py --host BL --resolved`

## Tier-1

| PDF | Curves | Text (Labels) | Collections/layers |
|-----|--------|---------------|-------------------|
| 1017 - Rev 0 | ☐ | ☐ | ☐ |
| webCapture | ☐ | ☐ | Hybrid raster |
| hello_world_rotated | ☐ | ☐ | Rotation |

## Automated

```powershell
python -m pytest tests/ -q -k "import_report"
```

BUILT. NOT BOUGHT.
