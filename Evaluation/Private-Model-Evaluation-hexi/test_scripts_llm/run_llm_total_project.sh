

with_location_json=$1
with_location_output_directory="generate_result_$(basename "$with_location_json" .json)"
with_location_test_result_directory="test_result_$(basename "$with_location_json" .json)"

python3 run_test_claude.py claude-sonnet-4-5 ../evaluation/hexi/$with_location_json $with_location_output_directory $with_location_test_result_directory  ../evaluation/hexi/test_cases 
python3 run_test_gpt.py gpt-5.1 ../evaluation/hexi/$with_location_json $with_location_output_directory $with_location_test_result_directory  ../evaluation/hexi/test_cases
python3 run_test_gpt.py gemini-3-pro-preview ../evaluation/hexi/$with_location_json $with_location_output_directory $with_location_test_result_directory  ../evaluation/hexi/test_cases
python3 run_test_ds.py deepseek-v3.1-terminus ../evaluation/hexi/$with_location_json $with_location_output_directory $with_location_test_result_directory  ../evaluation/hexi/test_cases
python3 run_test_private.py Qwen3-Coder-480B ../evaluation/hexi/$with_location_json $with_location_output_directory $with_location_test_result_directory  ../evaluation/hexi/test_cases

without_include_with_location_json=$2
without_include_output_directory="generate_result_$(basename "$without_include_with_location_json" .json)"
without_include_test_result_directory="test_result_$(basename "$without_include_with_location_json" .json)"

python3 run_test_claude_without_include.py claude-sonnet-4-5 ../evaluation/hexi/$without_include_with_location_json $without_include_output_directory $without_include_test_result_directory  ../evaluation/hexi/test_cases
python3 run_test_gpt_without_include.py gpt-5.1 ../evaluation/hexi/$without_include_with_location_json $without_include_output_directory $without_include_test_result_directory  ../evaluation/hexi/test_cases
python3 run_test_gpt_without_include.py gemini-3-pro-preview ../evaluation/hexi/$without_include_with_location_json $without_include_output_directory $without_include_test_result_directory  ../evaluation/hexi/test_cases
python3 run_test_ds_without_include.py deepseek-v3.1-terminus ../evaluation/hexi/$without_include_with_location_json $without_include_output_directory $without_include_test_result_directory  ../evaluation/hexi/test_cases
python3 run_test_private_without_include.py Qwen3-Coder-480B ../evaluation/hexi/$without_include_with_location_json $without_include_output_directory $without_include_test_result_directory  ../evaluation/hexi/test_cases
