# CodeBeacon

**A Lightweight and Model-Agnostic Code Entity Prediction Framework for Unseen Codebases**

## Overview

Large language models (LLMs) often exhibit poor performance and high hallucination rates on newly released software frameworks that are absent from their training data. Existing approaches offer partial remedies but face inherent limitations: **RAG** is limited by shallow similarity matching; **post-training internalization** couples domain knowledge and generation within a single model; **agentic code search** resorts to costly trial-and-error exploration.

**CodeBeacon** addresses these challenges by **decoupling domain knowledge acquisition from code generation**. A small-parameter prediction model (Qwen3-4B) is trained via annotation-free data synthesis, curriculum-guided SFT, and recall-centric RL to predict task-relevant code entities and their file locations. The source code of predicted entities is then supplied as precise context to any state-of-the-art LLM for generation — achieving a natural division of labor between a domain-knowledgeable small model and a capable large model.

### Key Results

- **vs. RAG**: Outperforms the best RAG baseline by **41.6%** (63.0% vs. 21.4% Avg. Pass@1)
- **vs. Post-training**: Outperforms the strongest post-training baseline by **27.0%** (63.0% vs. 36.0% Avg. Pass@1)
- **vs. Agentic Code Search**: Achieves comparable generation quality (63.0% vs. 63.9% Avg. Pass@1) while consuming **less than 1/11 of the tokens** and **1/8 of the LLM invocations**
- **Zero-hint Setting**: Outperforms end-to-end agentic generation (54.9% vs. 48.5%) with virtually no cost increase

## Framework

CodeBeacon operates through four main stages:

<p align="center">
  <img src="pictures/3_Approach/Overview.pdf" alt="CodeBeacon Overview" width="95%"/>
</p>

1. **Coverage-Driven Data Synthesis** — Automatically constructs a comprehensive training corpus from the unseen codebase without human annotation, including single-API usages and complex multi-API combinations, augmented with high-quality reasoning traces.

2. **Curriculum-Guided Prediction Training** — Empowers a lightweight model (Qwen3-4B) with domain-specific code entity prediction capabilities through SFT on synthesized reasoning data, followed by RL with a recall-centric reward and curriculum strategy.

3. **Index-Enriched Prediction Refinement** — Grounds the model's predictions against the actual codebase structure and expands partial matches using a bidirectional (name-to-path and path-to-name) index-driven enrichment mechanism.

4. **Context-Augmented Code Generation** — Extracts source code of refined entities to construct precise context, supplied to any state-of-the-art LLM for the final code generation.

## Repository Structure

```
CodeBeacon/
├── README.md
├── CodeBeacon/
│   ├── Coverage-DrivenDataSynthesis/          # Stage 1: Data Synthesis
│   │   ├── SingleAPI/                         # Single-API data generation
│   │   │   ├── generate_functional_description/
│   │   │   │   ├── generate_funcional_description.py   # Generate functional descriptions for each code entity
│   │   │   │   ├── functional_description_generation_cpp.j2    # Jinja2 template for C++ projects
│   │   │   │   ├── functional_description_generation_python.j2 # Jinja2 template for Python projects
│   │   │   │   ├── {hexi,leann,reaction,sqlgen}_*_normalized.json  # Codebase structural index
│   │   │   │   └── run.sh
│   │   │   └── reasoning_traces_generation/
│   │   │       ├── generate_application_answering_data_reasoning_content.py  # Sampling-based reasoning for single APIs
│   │   │       ├── prompt_single_api.j2       # Reasoning prompt template
│   │   │       ├── functional_description_total_*_similarity_*.json  # Hard negatives with similarity scores
│   │   │       └── generate.sh
│   │   ├── MultiAPICombination/               # Seed multi-API combination (from test cases)
│   │   │   └── generate_functional_description/
│   │   │       ├── generate_multi_api_combination_scenarios_description.py  # Extract & describe multi-API combos from tests
│   │   │       ├── functional_description_api_combination_generation_cpp.j2
│   │   │       ├── functional_description_api_combination_generation_python.j2
│   │   │       ├── {hexi,leann,reaction,sqlgen}_*_normalized.json
│   │   │       └── run.sh
│   │   └── SynthesisMultiAPICombination/      # Synthetic multi-API combination augmentation
│   │       ├── API_combination_synthesis/
│   │       │   ├── generate_api_combination_coherence_validation.py  # Bootstrap generation + coherence filtering
│   │       │   ├── functional_description_data_*/  # API catalog and seed data per project
│   │       │   └── run_coherence_validation.sh
│   │       └── reasoning_traces_generation/
│   │           ├── generate_multi_api_reasoning_content.py  # Rationalization-based reasoning for multi-API combos
│   │           ├── prompt_multi_api_rationalize.j2
│   │           ├── extracted_functionality_descriptions_*.json
│   │           └── generate_multi.sh
│   ├── Curriculum-GuidedPredictionTraining/    # Stage 2: Model Training
│   │   ├── SFT/
│   │   │   └── SFT_config.yaml                # LLaMA-Factory SFT config (Qwen3-4B, full-parameter)
│   │   └── RL/
│   │       ├── reward.py                       # Recall-centric reward function for GRPO
│   │       └── RL_config.yaml                  # verl/GRPO training config (Ray job)
│   └── Index-EnrichedPredictionRefinement/     # Stage 3: Post-processing
│       ├── refinement.py                       # Bidirectional index-driven enrichment & evaluation
│       ├── entity_name_to_file_path.json       # Name → Path index
│       ├── file_path_to_keyword.json           # Path → Name index
│       └── run.sh
└── Evaluation/                                 # Stage 4: Evaluation on UnseenCodeBench
    ├── run_llm_total.sh                        # Orchestration script for all experiments
    ├── Private-Model-Evaluation-hexi/          # Hexi project evaluation
    ├── Private-Model-Evaluation-reaction/      # Reaction project evaluation
    ├── Private-Model-Evaluation-sqlgen/        # sqlgen project evaluation
    ├── Private-Model-Evaluation-leann/         # LEANN project evaluation
    └── total_third_party_projects/             # Third-party codebase repositories
```

## Getting Started

### Prerequisites

- Python 3.8+
- Required packages: `openai`, `jinja2`, `sentence-transformers`
- For SFT: [LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory)
- For RL: [verl](https://github.com/volcengine/verl) with Ray
- Hardware: 8x NVIDIA H20 GPUs (for training)

### Stage 1: Coverage-Driven Data Synthesis

The data synthesis pipeline produces three categories of training data entirely from the source code of unseen codebases — no human annotation required.

#### 1.1 Single-API Functional Description Generation

Generate concise functional descriptions for each public code entity:

```bash
cd CodeBeacon/Coverage-DrivenDataSynthesis/SingleAPI/generate_functional_description/
# Example: generate descriptions for each project
python3 generate_funcional_description.py \
    <output_dir> <model_name> <project_name> <normalized_json> <template.j2> <num_processes>
# Or run all projects at once:
bash run.sh
```

#### 1.2 Seed Multi-API Combination Extraction

Extract multi-API combinations from built-in test cases:

```bash
cd CodeBeacon/Coverage-DrivenDataSynthesis/MultiAPICombination/generate_functional_description/
python3 generate_multi_api_combination_scenarios_description.py \
    <output_dir> <model_name> <project_name> <normalized_json> <template.j2> <num_processes>
# Or run all projects at once:
bash run.sh
```

#### 1.3 Multi-API Combination Bootstrap Augmentation

Iteratively bootstrap additional API combinations with coverage-driven sampling and coherence filtering:

```bash
cd CodeBeacon/Coverage-DrivenDataSynthesis/SynthesisMultiAPICombination/API_combination_synthesis/
python3 generate_api_combination_coherence_validation.py \
    --func_desc_dir <API_catalog_dir> \
    --output_dir <output_dir> \
    --model_name deepseek-v3.2 \
    --project_name <project_name> \
    --seed_tasks <seed_file1.json> <seed_file2.json> ... \
    --num_combinations 10 \
    --num_rounds 1 \
    --num_batches_per_round 5 \
    --apis_per_prompt 80 \
    --num_processes 10 \
    --coherence_threshold 4
# Or run all projects at once:
bash run_coherence_validation.sh
```

#### 1.4 Reasoning Traces Synthesis

**For single APIs** (sampling-based reasoning with hard negatives):

```bash
cd CodeBeacon/Coverage-DrivenDataSynthesis/SingleAPI/reasoning_traces_generation/
python3 generate_application_answering_data_reasoning_content.py \
    <model_name> <similarity_json_path> <num_processes>
bash generate.sh
```

**For multi-API combinations** (rationalization-based reasoning):

```bash
cd CodeBeacon/Coverage-DrivenDataSynthesis/SynthesisMultiAPICombination/reasoning_traces_generation/
python3 generate_multi_api_reasoning_content.py \
    <model_name> <extracted_descriptions.json> <project_name> -n <num_processes>
bash generate_multi.sh
```

### Stage 2: Curriculum-Guided Prediction Training

#### 2.1 Supervised Fine-Tuning (SFT)

Full-parameter SFT on the synthesized reasoning data using [LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory):

```bash
# Configure environment variables
export TRAIN_DATA_PATH=<path_to_sft_data>
export CKPT_PATH=<path_to_save_checkpoint>
export TF_EVENTS_PATH=<path_to_tensorboard_logs>
export llamafactory_template=<template_name>

# Run SFT with LLaMA-Factory using the provided config
llamafactory-cli train CodeBeacon/Curriculum-GuidedPredictionTraining/SFT/SFT_config.yaml
```

Key SFT hyperparameters (see `SFT_config.yaml`):
- Base model: `Qwen3-4B`
- Full-parameter fine-tuning with DeepSpeed ZeRO-3
- Learning rate: 5e-5, warmup ratio: 0.1
- Max sequence length: 32,768

#### 2.2 Reinforcement Learning (RL)

RL training with GRPO using the seed multi-API combination set and curriculum strategy:

```bash
# Configure environment variables
export TRAIN_DATA_PATH=<path_to_rl_data>
export CKPT_PATH=<path_to_save_checkpoint>
export HOST_GPU_NUM=8
export HOST_NUM=1

# Submit Ray job for GRPO training
# See CodeBeacon/Curriculum-GuidedPredictionTraining/RL/RL_config.yaml for full command
ray job submit --address="http://127.0.0.1:8265" \
    --runtime-env=runtime_env.yaml \
    -- python3 -m verl.trainer.main_ppo \
    algorithm.adv_estimator=grpo \
    data.train_files=$TRAIN_DATA_PATH \
    actor_rollout_ref.model.path=Qwen3-4B-SFT \
    actor_rollout_ref.rollout.n=16 \
    custom_reward_function.path=custom_reward.py \
    trainer.total_epochs=40 \
    ...
```

The recall-centric reward function (`reward.py`) implements:

$$R = \max\left(0,\; \underbrace{\frac{k}{N}}_{\text{recall}} - \underbrace{\lambda \cdot \frac{w}{N + w}}_{\text{penalty}}\right)$$

where $k$ is the number of correct matches, $N$ is the ground-truth count, $w$ is the number of incorrect outputs, and $\lambda = 0.5$.

### Stage 3: Index-Enriched Prediction Refinement

Ground predictions against the actual codebase structure and expand partial matches using bidirectional indices:

```bash
cd CodeBeacon/Index-EnrichedPredictionRefinement/
python3 refinement.py --eval-enrich \
    entity_name_to_file_path.json \
    file_path_to_keyword.json \
    $PREDICTION_RESULT_PATH
```

This stage performs:
1. **Structural grounding**: Prunes predictions with invalid file paths or unresolvable entity names
2. **Forward expansion** (Name → Paths): If an entity name maps to ≤ τ (default 3) candidate paths, all valid pairs are added
3. **Backward expansion** (Path → Names): All entities defined within a predicted file path are added
4. **Hallucination analysis**: Reports name/path/pair hallucination rates before and after enrichment

### Stage 4: Context-Augmented Code Generation & Evaluation

Extract the source code of refined entities and supply as context to any state-of-the-art LLM for generation:

```bash
cd Evaluation/
bash run_llm_total.sh
```

The evaluation is conducted on **UnseenCodeBench**, spanning 252 repository-level code generation tasks across four real-world unseen codebases:

| Project | Language | Domain |
|---------|----------|--------|
| sqlgen | C++ | Database systems |
| reaction | C++ | Binary data stream processing |
| Hexi | C++ | High-performance retrieval engine |
| LEANN | Python | Machine learning framework |
