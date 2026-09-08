# Original Fast Train Reference

## Source

- Project: Fast Train: Train Routing and Timetabling Engine.
- Public repository: https://github.com/xzhou99/Fast-train_for_train_timetabling/tree/master
- Selected local source: `/Users/liwenlin/Downloads/Fast-train_for_train_timetabling-master`
- Copy date: 2026-09-07.
- Public comparison: the local Git clone at `/Users/liwenlin/Downloads/ANL_RAS_asu/external_repos/Fast-train_for_train_timetabling` is at public commit `73cccb0e0ca80909cd150eac6b470d1be0e2e8c6` (2018-11-28). Hash comparison found all 299 tracked files identical. The selected Downloads tree additionally contains `GUI Release for RAS Problem Solving Competition/RAS_Toy_problem.zip`; therefore the selected tree is not byte-identical to public HEAD.
- Copy policy: all 300 non-`.DS_Store` files were copied. No source, data, PDF, ZIP, executable, DLL, Visual Studio database, result, or SVN metadata was omitted. Only six Finder `.DS_Store` cache files were excluded.

## Purpose

This directory preserves the original release for provenance and paper-to-implementation comparison. It is **not** the active ANL/RAS implementation and is not called by the current pipeline.

## Important Original Components

| Role | Original path |
|---|---|
| Paper | `Fast train A computationally efficient train routing and scheduling engine for general rail networks.pdf` |
| LR C++ framework | `LR-C++ solution framework/Fast train-V1.0-Min Total Delay Time/` |
| Program entry and CSV readers | `LR-C++ solution framework/Fast train-V1.0-Min Total Delay Time/FastTrain/FastTrain.cpp` (`g_ReadInputFiles`, `g_ReadNodeCSVFile`, `g_ReadMOWCSVFile`, `g_ReadLinkCSVFile`, `g_ReadTrainInfoCSVFile`, `_tmain`) |
| Time-space routing/DP | `LR-C++ solution framework/Fast train-V1.0-Min Total Delay Time/FastTrain/ShortestPath.cpp` (`BuildSpaceTimeNetworkForTimetabling`, `OptimalTDLabelCorrecting_DoubleQueue`, `FindOptimalSolution`) |
| LR and feasible recovery | `LR-C++ solution framework/Fast train-V1.0-Min Total Delay Time/FastTrain/Timetable.cpp` (`g_Timetable_Optimization_Lagrangian_Method`, `g_UpdateResourcePrices`, `g_DeduceToProblemFeasibleSolution`) |
| Network/train/resource classes | `LR-C++ solution framework/Fast train-V1.0-Min Total Delay Time/FastTrain/Network.h` |
| General Fast Train inputs | `LR-C++ solution framework/Fast train-V1.0-Min Total Delay Time/FastTrain/input_node.csv`, `input_link.csv`, `input_train_info.csv`, `input_MOW.csv` |
| Schedule output | `FastTrain.cpp::g_ExportTimetableDataToCSVFile`, `FastTrain.cpp::g_ExportTimetableDataToXMLFile`, and `FastTrain/internal_timetable/` |
| RAS competition release | `GUI Release for RAS Problem Solving Competition/` |
| RAS inputs | `GUI Release for RAS Problem Solving Competition/RAS_data-set_1/`, `RAS_data-set_2/`, `RAS_data-set_3/`, and `RAS_Toy_problem/` |
| RAS format/GUI documentation | `GUI Release for RAS Problem Solving Competition/NEXTA Users Guide_for_RAS.pdf` and `.docx` |
| GUI/visualization | `GUI Release for RAS Problem Solving Competition/NEXTA.exe` |
| GUI feasibility check | `NEXTA.exe`; the user guide states that headway, nonconcurrency, and MOW are checked. Source for this binary was not located. |

The preserved source contains the general Fast Train C++ readers for `input_node.csv` and `input_link.csv`, but no source-level reader for RAS filenames such as `input_rail_arc.csv`. The executable RAS adapter inside `NEXTA.exe` cannot be audited from this release.
