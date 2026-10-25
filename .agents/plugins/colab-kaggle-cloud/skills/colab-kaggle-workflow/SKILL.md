---
name: colab-kaggle-workflow
description: >-
  Use this skill when the user asks to create, inspect, or modify Jupyter/Colab/Kaggle
  notebooks (.ipynb), select GPU/TPU hardware accelerators (T4, P100, L4, A100, V100, TPU, CPU),
  execute notebooks on Google Colab or Kaggle Cloud Kernels, or export/download output data and datasets.
---

# Colab & Kaggle Cloud Notebook Workflow

This workspace includes the `colab-kaggle-notebook` MCP server ([notebook_cloud_mcp.py](file:///D:/Antigravity_playgournd/mcp_servers/notebook_cloud_mcp.py)) and the `kaggle-official-remote` MCP endpoint (`https://www.kaggle.com/mcp`).

## Available MCP Tools (`colab-kaggle-notebook`)

1. **`notebook_inspect`**
   - Inspects `.ipynb` cell structure, existing outputs, Colab GPU/TPU settings, and Kaggle `kernel-metadata.json`.
2. **`notebook_modify_cell`**
   - Creates a new `.ipynb` or runs `append`, `insert`, `replace`, or `delete` on `code` or `markdown` cells.
3. **`notebook_select_hardware`**
   - Sets `hardware` (`CPU`, `T4`, `T4x2`, `P100`, `V100`, `L4`, `A100`, `TPU`) and `high_ram` (`true`/`false`) for:
     - **Google Colab**: Writes `metadata.accelerator` and `metadata.colab.gpuType` directly into the `.ipynb` file.
     - **Kaggle Kernels**: Generates/updates `kernel-metadata.json` with `enable_gpu`, `enable_tpu`, `Accelerator`, and attached datasets/competitions.
4. **`kaggle_execute_notebook`**
   - Pushes the `.ipynb` and `kernel-metadata.json` to Kaggle Cloud Kernels (`kaggle kernels push` or Kaggle REST API v1) for remote execution on the selected GPU/TPU.
5. **`kaggle_export_data`**
   - Checks remote execution status and downloads generated output files, logs, or datasets into `storage/exports/`.
6. **`colab_execute_notebook`**
   - Executes `.ipynb` cells sequentially, saves cell outputs back into the `.ipynb`, and exports generated artifacts to `storage/exports/`.
   - For live browser-hosted Colab VM sessions, you can also attach Google's official `colab-mcp` (`uvx git+https://github.com/googlecolab/colab-mcp`).

## Standard Operating Procedure

1. **Modify / Create Notebook**: Call `notebook_modify_cell` to add or edit code/markdown cells in `notebooks/<name>.ipynb`.
2. **Select Hardware Accelerator**: Call `notebook_select_hardware` with the requested GPU/TPU (e.g., `hardware="A100"` or `hardware="T4"`, `platform="both"`).
3. **Execute Notebook**:
   - For Kaggle Cloud GPU/TPU execution: Call `kaggle_execute_notebook`.
   - For local/Colab runtime execution: Call `colab_execute_notebook`.
4. **Export Data**: Call `kaggle_export_data` (or inspect `storage/exports/`) to verify downloaded CSVs, model weights, or plots.
