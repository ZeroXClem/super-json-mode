import json
from collections import defaultdict
import time
import os
from tqdm import tqdm
from torch.utils.data import Dataset
from prettytable import PrettyTable
from itertools import islice
import numpy as np
from .utils import JSONBatcher, JSONDataset
from .utils.postprocessing import *

class JSONGenerator:
    def __init__(self, dataset_file):
        self.dataset_file = dataset_file
        self.dataset = self.load_dataset(dataset_file)

    def load_dataset(self, dataset_file):
        with open(dataset_file, "r") as f:
            dataset = [json.loads(line) for line in f.readlines()]
        return dataset

    def has_matching_schema(self, output, target):

        if type(output) is not type(target):
          return False

        output_keys = output.keys()
        target_keys = target.keys()

        if output_keys != target_keys:
          return False

        else:
          for key in output_keys:
            if type(output[key]) is dict:
              if not self.has_matching_schema(output[key], target[key]):
                return False

        return True

    def generate_prompt(self, passage, schema):
        user_message = f"""{passage}
        From the above passage, extract the following schema: {schema}

        Only output JSON with the allowed types."""

        prompt = f"""<s><<SYS>>You only respond in JSON. You do not add text before. You do not add text after. Only JSON. <</SYS>>[INST] {user_message} [/INST]"""
        return prompt
    
    def run(self, generate, batch_size, out = True, out_dir = os.getcwd(), **sampling_params):

        evals = []
        outputs = []
        run_times = []

        #dataset generator object from raw JSON file
        batcher = JSONBatcher(self.dataset_file)
        data, schemas, original_properties, prompt_ids = batcher.get_dataset(self.generate_prompt)

        #Initialize Hugging Face Dataset object
        dataset = JSONDataset(data)

        start_time = time.time()

        for out in tqdm(generate(dataset, batch_size = batch_size, **sampling_params)):
            time_taken = round(time.time() - start_time, 3)
            run_times.append(time_taken)
            outputs.append(out)
            start_time = time.time()

        for output, run_time, schema in zip(outputs, run_times, schemas):
            evaluation = {}

            result = output[0]["generated_text"].strip()
            result = result.replace("\'", "\"")

            evaluation["generation"] = result
            evaluation["time_taken"] = time_taken

            # check if result is valid JSON
            try:
                json_result = json.loads(result)
                evaluation["is_valid"] = True

                # check if result matches schema
                # JSON might have erroneous keys
                evaluation["matches_schema"] = self.has_matching_schema(json_result, schema)
                evaluation["error_type"] = None
            except ValueError:
                evaluation["is_valid"] = False
                evaluation["matches_schema"] = False

                if result[0] != "{":
                    evaluation["error_type"] = "prefix"
                elif result[-1] != "}":
                    evaluation["error_type"] = "suffix"
                else:
                    evaluation["error_type"] = "invalid"

            evaluation["batch_size"] = batch_size
            evals.append(evaluation)


        cleaned_outputs = clean_outputs(outputs)

        if out:
          write_outputs(self.dataset_file, out_dir, cleaned_outputs, 2, sampling_params)
        
        output_jsons = build_json(cleaned_outputs, original_properties, prompt_ids)

        return output_jsons, evals

    def print(self, evals, show_generation=False):
        table = PrettyTable()

        # Define the table columns
        table.field_names = [
            "Valid (✅/❌)",
            "Matches Schema (✅/❌)",
            "Batch Size",
            "Time (s)",
            "Error",
        ]
        if show_generation:
            table.add_column("Generation")

        valid_counter, schema_counter, total_time = 0, 0, 0

        for eval in evals:
            is_valid = "✅" if eval["is_valid"] else "❌"
            matches_schema = "✅" if eval["matches_schema"] else "❌"
            error_type = eval["error_type"]
            batch_size = eval["batch_size"]

            valid_counter += eval["is_valid"]
            schema_counter += eval["matches_schema"]
            total_time += eval["time_taken"]

            row = [is_valid, matches_schema, batch_size, eval["time_taken"], error_type]
            if show_generation:
                row.append(eval["generation"])

            table.add_row(row)

        valid_accuracy = valid_counter / len(evals)
        schema_accuracy = schema_counter / len(evals)
        average_time = round(total_time / len(evals), 3)

        table.add_row(["-", "-", "-", "-", "-"])
        table.add_row(
            [
                f"Accuracy: {valid_accuracy}",
                f"Accuracy: {schema_accuracy}",
                "-",
                f"Average: {average_time}",
                "-",
            ]
        )

        print(table)