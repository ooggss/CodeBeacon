
# reaction 项目
python3 generate_api_combination_coherence_validation.py \
    --func_desc_dir functional_description_data_total_0_fixed \
    --output_dir api_combination_data_reaction \
    --model_name deepseek-v3.2 \
    --project_name reaction \
    --seed_tasks functional_description_data_api_combination_reaction/reaction/generated_functional_description_of_api_combination_deepseek-v3.2_20260307_015805_69.json functional_description_data_api_combination_reaction/reaction/generated_functional_description_of_api_combination_deepseek-v3.2_20260307_015924_69.json functional_description_data_api_combination_reaction/reaction/generated_functional_description_of_api_combination_deepseek-v3.2_20260307_020042_69.json \
    --num_combinations 10 \
    --num_rounds 1 \
    --num_batches_per_round 5 \
    --apis_per_prompt 80 \
    --num_processes 10 \
    --coherence_threshold 4
# # hexi 项目
python3 generate_api_combination_coherence_validation.py \
    --func_desc_dir functional_description_data_total_0_fixed \
    --output_dir api_combination_data_hexi \
    --model_name deepseek-v3.2 \
    --project_name Hexi \
    --seed_tasks functional_description_data_api_combination_hexi/Hexi/generated_functional_description_of_api_combination_deepseek-v3.2_20260307_021108_186.json functional_description_data_api_combination_hexi/Hexi/generated_functional_description_of_api_combination_deepseek-v3.2_20260307_021336_186.json functional_description_data_api_combination_hexi/Hexi/generated_functional_description_of_api_combination_deepseek-v3.2_20260307_021543_186.json \
    --num_combinations 10 \
    --num_rounds 1 \
    --num_batches_per_round 5 \
    --apis_per_prompt 80 \
    --num_processes 10 \
    --coherence_threshold 4 
# # LEANN 项目
python3 generate_api_combination_coherence_validation.py \
    --func_desc_dir functional_description_data_total_0_fixed \
    --output_dir api_combination_data_leann \
    --model_name deepseek-v3.2 \
    --project_name LEANN \
    --seed_tasks functional_description_data_api_combination_leann/LEANN/generated_functional_description_of_api_combination_deepseek-v3.2_20260307_022056_82.json functional_description_data_api_combination_leann/LEANN/generated_functional_description_of_api_combination_deepseek-v3.2_20260307_022301_82.json functional_description_data_api_combination_leann/LEANN/generated_functional_description_of_api_combination_deepseek-v3.2_20260307_022502_82.json \
    --num_combinations 10 \
    --num_rounds 1 \
    --num_batches_per_round 5 \
    --apis_per_prompt 80 \
    --num_processes 10 \
    --coherence_threshold 4 \
# sqlgen 项目
python3 generate_api_combination_coherence_validation.py \
    --func_desc_dir functional_description_data_total_0_fixed \
    --output_dir api_combination_data_sqlgen \
    --model_name deepseek-v3.2 \
    --project_name sqlgen \
    --seed_tasks functional_description_data_api_combination_sqlgen/sqlgen/generated_functional_description_of_api_combination_deepseek-v3.2_20260307_020333_199.json functional_description_data_api_combination_sqlgen/sqlgen/generated_functional_description_of_api_combination_deepseek-v3.2_20260307_020621_199.json functional_description_data_api_combination_sqlgen/sqlgen/generated_functional_description_of_api_combination_deepseek-v3.2_20260307_020855_199.json \
    --num_combinations 10 \
    --num_rounds 1 \
    --num_batches_per_round 5 \
    --apis_per_prompt 80 \
    --num_processes 10 \
    --coherence_threshold 4 \