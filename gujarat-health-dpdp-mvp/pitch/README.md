# Pitch deck

`build_deck.cjs` generates the 20-slide PowerPoint pitch for the Health and Family Welfare Department, Gujarat. It covers background, workflow, the proposed solution and next steps, with speaker notes on every slide. The finished .pptx is delivered separately and is not stored in git.

```bash
./start.sh --reset                        # demo data for the screenshots
# capture 1920x1080 screenshots named overview, decrypt, engine, processors, breach, ledger, governance into shots/
npm install pptxgenjs jszip
PPTX_SKILL=<path to the pptx skill> node build_deck.cjs shots Gujarat_Health_DPDP_Pitch.pptx
```

The theme uses navy (002060) and gold (C59B27) in Calibri. Change colours in `THEME` and the text in each slide block.
