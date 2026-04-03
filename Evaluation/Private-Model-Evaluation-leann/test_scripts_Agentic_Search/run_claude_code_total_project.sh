
original_json=$1
original_output_directory="generate_result_$(basename "$original_json" .json)_original"
original_test_result_directory="test_result_$(basename "$original_json" .json)_original"
python3 run_test_gpt_with_claude_code_for_keyword.py claude-sonnet-4-5 ../evaluation/leann/$original_json $original_output_directory /data/workspace/AgenticSearch/total_third_party_projects $original_test_result_directory ../evaluation/LEANN/test_cases

