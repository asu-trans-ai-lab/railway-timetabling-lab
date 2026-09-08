# Source provenance

## Inputs

- Integration date: 2026-09-08.
- Historical predecessor: `/Users/liwenlin/Downloads/GPT_sample_code.zip` (`SHA256 12b85c23cc002a8b9d202b2df16f7f49499fe1f4a85e34c9fab7994e2838d48a`).
- Authoritative source: `/Users/liwenlin/Downloads/ras_idp_lab_v2.zip` (`SHA256 3dff010b0b81007216fef9c3df14bde5c9a67640650103f57887deb8b711a796`).

`GPT_sample_code.zip` was recorded only as historical provenance. It was not merged, copied, or used to overwrite v2.

## Copied source

The following authoritative v2 directory was copied recursively into `idp/prototype/` while preserving original relative paths:

```text
ras_idp_lab_v2/prototype/
|-- root design/status documentation and run_prototype.sh
|-- idp_lab/
`-- resource_reservation_lite/
```

This includes every non-generated Python source file, canonical JSON case, test, shell runner, Markdown design document, Makefile, and C++ source file in that tree. Copies of v2 production directories such as `solver/`, `data/`, `adapters/`, and `validator/` were intentionally not copied because this integration adds only the parallel theoretical track.

## Intentional exclusions

The authoritative prototype tree contained 212 files. Exactly 162 runtime/generated files were excluded:

- `ras_idp_lab_v2/prototype/idp_lab/results/`: 82 pre-generated result files.
- `ras_idp_lab_v2/prototype/results/`: 79 pre-generated result files.
- `ras_idp_lab_v2/prototype/resource_reservation_lite/cpp/reservation_bb`: one precompiled Linux x86-64 ELF executable; the preserved `Makefile` and `reservation_bb.cpp` rebuild it locally.

No source, tests, cases, design documentation, Makefiles, shell runners, or C++ source were excluded. Cache categories (`__pycache__`, `.pytest_cache`, `*.pyc`, `.DS_Store`) are excluded by policy; none existed in the authoritative prototype tree inside the ZIP.

## Preservation result

- Authoritative non-generated files: 50.
- Files copied: 50.
- Byte-identical files: 50.
- Modified or missing files: 0.
- Extra files inside the preserved tree: 0.
- Source tree SHA256: `8e44dcf8bb219efbe087d104903359d83364ee0edc96426a21ba9938ca6a1b9b`.
- Copy tree SHA256: `8e44dcf8bb219efbe087d104903359d83364ee0edc96426a21ba9938ca6a1b9b`.

`PRESERVATION_MANIFEST.tsv` records the source and copied SHA256 for every preserved file. No authoritative Zhou source file was modified.
