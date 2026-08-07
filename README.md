<p align="center">
 <img width="200" height="154.5" alt="Fusion-Logo-Navigator-Color01" src="https://github.com/user-attachments/assets/c6c7c6d6-cb07-41c4-ba04-68666251829b"/> </p>

# FUSION++ 

![Python](https://img.shields.io/badge/python-%3E%3D3.9-blue)

> A Python-packaged version of FUSION in the JupyterHub interface for spatial multi-omics analysis with seamless HuBMAP data integration.

> FUSION++ brings together the full spatial biology analysis pipeline — from raw data access to interactive visualization — inside a collaborative JupyterHub notebook environment. Whether you're a biologist exploring kidney tissue, a bioinformatician running segmentation workflows, or a data scientist building custom visualizations, FUSION++ provides the tools to work together in a single, secure, reproducible environment.

---

## Overview

To streamline user experience and broaden accessibility, FUSION++ now integrates a fully managed JupyterHub environment as a core component of its open-source offering. This pre-configured deployment removes the technical barriers to entry, enabling a diverse range of users—from students and computational biologists to clinical and translational researchers—to instantly launch interactive notebooks with full FUSION++ functionality, eliminating the need for complex manual installation or environment configuration.
 
By embedding JupyterHub directly into the FUSION++ ecosystem, we provide more than just a coding interface; we offer a modular, high-performance platform optimized for the analysis of spatial omics and histology data. This integration leverages robust compute resources and a secure enclave, ensuring that user data remains private while benefiting from a standardized Python environment. Within this workspace, researchers can execute a complete, reproducible workflow: fetching large-scale datasets (such as those from the HuBMAP consortium), running complex spatial analyses, and performing interactive visualizations using the integrated HitomicUI.
 
Beyond these built-in tools, the platform provides immense flexibility for advanced users to develop custom workflows and generate bespoke visualizations programmatically. While HitomicUI offers powerful interactive capabilities, the notebook environment allows researchers to extend FUSION++'s functionality to create specialized plots using Fusion++ analysis results—such as Volcano plots for differential expression or complex Heatmaps for gene mapping—that are not natively available on the standard platform. This capability transforms FUSION++ from a visualization tool into a flexible research engine, allowing users to tailor their analysis to the specific statistical needs of their study.
 
This integrated environment does not only simplify onboarding but fundamentally accelerates the journey from visual observation to biological discovery. By empowering users to answer the critical questions of “what does it look like,” “what is it doing biologically,” and “where is it happening spatially,” FUSION++ enables the extraction of high-resolution insights across diverse computational settings. As we move toward FUSION 2.0, this ecosystem will continue to evolve, incorporating agentic AI for molecular discovery and expanding support for single-cell spatial omics, further cementing its role as a cross-disciplinary tool for the global research community.


![FUSION++ Workflow Diagram](fusion/images/data_sources.png)

---

## Key Features

- **Easy Data Access**
Direct integration with the HuBMAP Data Portal lets you fetch datasets using a single HuBMAP ID — no manual downloads or tool setup required.

- **Streamlined Workflow**
A guided, step-by-step notebook walks you from data fetch → analysis → visualization → annotation → custom plotting. Each section is self-contained and collapsible for easy navigation.

- **Custom Visualization**
Go beyond default plots. FUSION++ supports richly customizable outputs, including spatial overlays, volcano plots, heatmaps, and cell-type composition charts — all built on your own analysis results.

- **Diverse Collaboration**
Biologists, bioinformaticians, and data scientists can work side-by-side in the same JupyterHub notebook, with built-in documentation and flowcharts that make the platform an effective teaching resource.

- **Secure Enclave**
All data and analysis outputs are persisted privately within your workspace. Your data never leaves your environment.

- **Training Platform**
The notebook's integrated flowcharts, documentation, and step-by-step structure make it an excellent resource for teaching spatial multi-omics workflows.
---

## Quick Start

https://github.com/SarderLab/FUSION-Notebook-Tutorial

---
## Workflow

| # | Workflow | Description |
|---|----------|-------------|
| 1 | Multi-Compartment Segmentation | Segments WSIs into 6 tissue compartments (cortical interstitium, medullary interstitium, non-sclerotic glomerulus, sclerotic glomerulus, tubule) |
| 2 | Frozen Glomerulus Segmentation | Segments frozen tissue sections into individual glomeruli |
| 3 | Expanded Granular Feature Extraction | Extracts 72 morphometric features per FTU (Functional Tissue Unit) |
| 4 | Label Transfer | Predicts cell type composition (L1/L2) for each Visium spot using a reference atlas |
| 5 | Spot Annotation | Aligns Visium spots to the WSI and embeds cellular information |
| 6 | Spatial Aggregation | Aggregates FTUs with generated spot data for downstream analysis |

## Links

- **HuBMAP Data Portal:** [portal.hubmapconsortium.org](https://portal.hubmapconsortium.org/)
- **FUSION Platform:** [fusionpub.rc.ufl.edu](https://fusionpub.rc.ufl.edu)
- **Source Code:** [github.com/SarderLab/fusion-packages](https://github.com/SarderLab/fusion-packages)
- **FUSION1.0 Plugins** [github.com/SarderLab/FUSION_1.0_Plugins](https://github.com/SarderLab/FUSION_1.0_Plugins)
- **FUSION2.0 Plugins** [github.com/SarderLab/FUSION_2.0_Plugins](https://github.com/SarderLab/FUSION_2.0_Plugins)
---
