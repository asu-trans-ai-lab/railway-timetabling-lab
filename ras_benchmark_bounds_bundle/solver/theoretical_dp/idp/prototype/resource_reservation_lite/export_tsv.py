from __future__ import annotations

import argparse
import csv
from pathlib import Path

from .io import load_instance


def export_tsv(instance_json: str | Path, output_tsv: str | Path, *, time_step: float | None = None, headway: float | None = None) -> Path:
    instance = load_instance(instance_json, time_step_min=time_step, safety_headway_min=headway)
    output = Path(output_tsv)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream, delimiter='\t', lineterminator='\n')
        writer.writerow(['instance','time_step_min','headway_min','train_id','release_min','train_type','task_index','resource_id','duration_min'])
        for job in instance.jobs:
            for index, task in enumerate(job.tasks):
                writer.writerow([instance.name, instance.time_step_min, instance.safety_headway_min,
                                 job.train_id, job.release_min, job.train_type, index,
                                 task.resource_id, task.duration_min])
    return output


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--instance', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--time-step', type=float)
    parser.add_argument('--headway', type=float)
    args=parser.parse_args()
    print(export_tsv(args.instance,args.output,time_step=args.time_step,headway=args.headway))

if __name__=='__main__': main()
