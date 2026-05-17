# Repair Photo Model

Photo-based repair detection is separate from `main.py`.

The model uses existing `ремонт` values as weak labels, downloads listing photos, extracts local visual features, and trains a small sklearn classifier. It does not use an external AI API.

## Train

First export a manual labeling file and photo contact sheets:

```bash
python scripts/export_repair_labels.py
```

This creates:

- `data/repair_photo_labels.csv`
- `data/repair_photo_label_sheets/*.jpg`

Fill `manual_repair` in `data/repair_photo_labels.csv` with one of:

- `радянський ремонт`
- `євроремонт`
- `під ремонт`
- `косметичний ремонт`

Manual labels have priority over weak text labels during training.

```bash
python scripts/repair_photo_model.py train \
  --sleep 0.8 \
  --max-images 4 \
  --min-samples 8 \
  --labels-csv data/repair_photo_labels.csv
```

Output:

- model: `models/repair_photo_model_4class.json`
- image cache: `data/repair_photo_images`

## Apply

```bash
python scripts/repair_photo_model.py apply \
  --sleep 0.8 \
  --max-images 4 \
  --min-confidence 0.45
```

The script fills only empty `ремонт` values by default.

Useful options:

- `--overwrite`: replace existing `ремонт` values.
- `--max-rows 10`: process a small batch.
- `--min-confidence 0.75`: stricter predictions.
- `--min-confidence 0.0 --overwrite`: force every row into one of the target classes.

Current model classes:

- `радянський ремонт`
- `євроремонт`
- `під ремонт`
- `косметичний ремонт`

Because the labels are weak and the dataset is still small, use a confidence threshold when you want stricter predictions. Use `--min-confidence 0.0 --overwrite` only when the CSV must contain one of the four target labels for every row.
