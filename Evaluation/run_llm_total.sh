# Agentic Search
# Search
cd Private-Model-Evaluation-hexi/test_scripts
./run_claude_code_total_project.sh ../../Agentic_Search_original.jsonl
cd ../../Private-Model-Evaluation-reaction/test_scripts 
./run_claude_code_total_project.sh ../../Agentic_Search_original.jsonl
cd ../../Private-Model-Evaluation-sqlgen/test_scripts 
./run_claude_code_total_project.sh ../../Agentic_Search_original.jsonl
cd ../../Private-Model-Evaluation-leann/test_scripts
./run_claude_code_total_project.sh ../../Agentic_Search_original.jsonl

cd Private-Model-Evaluation-hexi/test_scripts
./run_claude_code_total_project.sh ../../Agentic_Search_Zero-Hint.jsonl
cd ../../Private-Model-Evaluation-reaction/test_scripts 
./run_claude_code_total_project.sh ../../Agentic_Search_Zero-Hint.jsonl
cd ../../Private-Model-Evaluation-sqlgen/test_scripts 
./run_claude_code_total_project.sh ../../Agentic_Search_Zero-Hint.jsonl
cd ../../Private-Model-Evaluation-leann/test_scripts
./run_claude_code_total_project.sh ../../Agentic_Search_Zero-Hint.jsonl

# Generation
(cd Private-Model-Evaluation-hexi/test_scripts_llm && ./run_llm_total_project.sh RQ1/test_cases_for_Agentic_Search_keyword_results_original.json RQ1/test_cases_for_Agentic_Search_keyword_results_Zero-Hint.json) &
(cd Private-Model-Evaluation-reaction/test_scripts_llm && ./run_llm_total_project.sh RQ1/test_cases_for_Agentic_Search_keyword_results_original.json RQ1/test_cases_for_Agentic_Search_keyword_results_Zero-Hint.json) &
(cd Private-Model-Evaluation-sqlgen/test_scripts_llm && ./run_llm_total_project.sh RQ1/test_cases_for_Agentic_Search_keyword_results_original.json RQ1/test_cases_for_Agentic_Search_keyword_results_Zero-Hint.json) &
(cd Private-Model-Evaluation-leann/test_scripts_llm && ./run_llm_total_project.sh RQ1/test_cases_for_Agentic_Search_keyword_results_original.json RQ1/test_cases_for_Agentic_Search_keyword_results_Zero-Hint.json) 

wait

# Agent
cd Private-Model-Evaluation-hexi/test_scripts
./run_claude_code_total_project.sh RQ1/test_cases_for_original.json RQ1/test_cases_for_Agent_Zero-Hint.json
cd ../../Private-Model-Evaluation-reaction/test_scripts 
./run_claude_code_total_project.sh RQ1/test_cases_for_original.json RQ1/test_cases_for_Agent_Zero-Hint.json
cd ../../Private-Model-Evaluation-sqlgen/test_scripts 
./run_claude_code_total_project.sh RQ1/test_cases_for_original.json RQ1/test_cases_for_Agent_Zero-Hint.json
cd ../../Private-Model-Evaluation-leann/test_scripts
./run_claude_code_total_project.sh RQ1/test_cases_for_original.json RQ1/test_cases_for_Agent_Zero-Hint.json

# LLM
(cd Private-Model-Evaluation-hexi/test_scripts_llm && ./run_llm_total_project.sh RQ1/test_cases_CodeBeacon_original.json RQ1/test_cases_CodeBeacon_Zero-Hint.json) &
(cd Private-Model-Evaluation-reaction/test_scripts_llm && ./run_llm_total_project.sh RQ1/test_cases_CodeBeacon_original.json RQ1/test_cases_CodeBeacon_Zero-Hint.json) &
(cd Private-Model-Evaluation-sqlgen/test_scripts_llm && ./run_llm_total_project.sh RQ1/test_cases_CodeBeacon_original.json RQ1/test_cases_CodeBeacon_Zero-Hint.json) &
(cd Private-Model-Evaluation-leann/test_scripts_llm && ./run_llm_total_project.sh RQ1/test_cases_CodeBeacon_original.json RQ1/test_cases_CodeBeacon_Zero-Hint.json) 

wait
