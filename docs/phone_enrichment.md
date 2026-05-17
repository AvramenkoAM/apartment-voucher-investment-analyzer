# Phone Enrichment

OLX phone collection is separate from `main.py`.

## Run As Foreground Process

```bash
python scripts/enrich_olx_phones_browser.py \
  --headless \
  --manual-wait 0 \
  --phone-wait 15 \
  --sleep 5
```

Useful options:

- `--phone-wait 15`: wait after clicking "show phone".
- `--sleep 5`: wait between listings.
- `--max-rows 10`: process only a small batch.
- omit `--headless`: open a visible browser if OLX requires manual verification.

The script writes every found phone to CSV immediately, so progress is not lost if the process stops.

## Run In Background

```bash
scripts/start_olx_phone_enrichment.sh
```

The launcher writes:

- log file: `logs/olx_phone_enrichment_YYYYMMDD_HHMMSS.log`
- pid file: `logs/olx_phone_enrichment.pid`

Check progress:

```bash
tail -f logs/olx_phone_enrichment_*.log
```

Stop process:

```bash
kill "$(cat logs/olx_phone_enrichment.pid)"
```
