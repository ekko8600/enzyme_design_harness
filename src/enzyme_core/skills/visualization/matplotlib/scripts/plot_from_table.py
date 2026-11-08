#!/usr/bin/env python3
"""Plot actual tabular inputs from declarative JSON; never synthesize scientific data."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--provenance', default='plot_provenance.json')
    args = parser.parse_args()
    data_path, config_path = Path(args.input), Path(args.config)
    config = json.loads(config_path.read_text(), parse_constant=lambda x: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))
    permitted = {'kind', 'x', 'y', 'title', 'xlabel', 'ylabel', 'x_unit', 'y_unit', 'bins'}
    if not isinstance(config, dict) or set(config) - permitted:
        raise ValueError('PLOT_CONFIG_UNKNOWN_FIELD')
    if not {'kind', 'x', 'y', 'xlabel', 'ylabel', 'x_unit', 'y_unit'}.issubset(config):
        raise ValueError('PLOT_CONFIG_LABELS_UNITS_REQUIRED')
    if config['kind'] not in {'line', 'scatter', 'bar', 'histogram'}:
        raise ValueError('PLOT_KIND_UNSUPPORTED')
    with data_path.open(newline='') as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        rows = list(reader)
    if not rows or config['x'] not in columns or (config['kind'] != 'histogram' and config['y'] not in columns):
        raise ValueError('PLOT_COLUMNS_OR_DATA_MISSING')
    def numbers(column):
        values = [float(row[column]) for row in rows]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('PLOT_VALUES_NONFINITE')
        return values
    x = [row[config['x']] for row in rows] if config['kind'] == 'bar' else numbers(config['x'])
    y = numbers(config['y']) if config['kind'] != 'histogram' else None
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 4), layout='constrained')
    if config['kind'] == 'histogram':
        bins = config.get('bins', 10)
        if not isinstance(bins, int) or isinstance(bins, bool) or not 1 <= bins <= 200:
            raise ValueError('PLOT_BINS_INVALID')
        ax.hist(x, bins=bins)
    else:
        getattr(ax, 'plot' if config['kind'] == 'line' else config['kind'])(x, y)
    ax.set_title(config.get('title', ''))
    ax.set_xlabel(config['xlabel'] + (f" ({config['x_unit']})" if config['x_unit'] else ''))
    ax.set_ylabel(config['ylabel'] + (f" ({config['y_unit']})" if config['y_unit'] else ''))
    out = Path(args.output)
    if out.suffix.lower() not in {'.png', '.svg', '.pdf'}:
        raise ValueError('PLOT_OUTPUT_FORMAT_UNSUPPORTED')
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    plt.close(fig)
    record = {'schema_version': 'enzyme-core/table-plot/v1', 'input_sha256': hashlib.sha256(data_path.read_bytes()).hexdigest(),
              'config_sha256': hashlib.sha256(config_path.read_bytes()).hexdigest(), 'input': str(data_path.resolve()),
              'config': config, 'row_count': len(rows), 'synthetic_data': False, 'scientific_validation': 'NOT_ASSESSED'}
    Path(args.provenance).write_text(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
