from transformers import AutoTokenizer, T5Model
import torch
import json
import pytest
import tt_lib
from loguru import logger

from tests.python_api_testing.models.utility_functions_new import (
    Profiler,
    disable_persistent_kernel_cache,
    enable_persistent_kernel_cache,
    prep_report,
)
from models.t5.tt.t5_model import TtT5Model


@pytest.mark.parametrize(
    "expected_inference_time, expected_compile_time",
    (
        (
            0.1,
            5.1,
        ),
    ),
)
def test_perf(use_program_cache, expected_inference_time, expected_compile_time):
    profiler = Profiler()
    disable_persistent_kernel_cache()
    comments = "small"
    first_key = "first_iter"
    second_key = "second_iter"
    cpu_key = "ref_key"

    use_attention_mask = True

    # Initialize the device
    device = tt_lib.device.CreateDevice(tt_lib.device.Arch.GRAYSKULL, 0)
    tt_lib.device.InitializeDevice(device)
    tt_lib.device.SetDefaultDevice(device)

    tokenizer = AutoTokenizer.from_pretrained("t5-small", model_max_length=32)
    hf_reference_model = T5Model.from_pretrained("t5-small")
    hf_reference_model.eval()

    config = json.loads(hf_reference_model.config.to_json_string())
    tt_model = TtT5Model(config, hf_reference_model.state_dict(), device)

    # Prepare input
    input_sentance = "Studies have been shown that owning a dog is good for you"
    tokenized = tokenizer(
        input_sentance, padding="max_length", max_length=32, return_tensors="pt"
    )  # Batch size 1

    input_ids = tokenized.input_ids
    attention_mask = tokenized.attention_mask if use_attention_mask else None

    decoder_input_sentence = "Studies show that"
    tokenized = tokenizer(
        decoder_input_sentence, padding="max_length", max_length=32, return_tensors="pt"
    )  # Batch size 1

    decoder_input_ids = tokenized.input_ids
    decoder_attention_mask = tokenized.attention_mask if use_attention_mask else None

    # preprocess: Prepend decoder_input_ids with start token which is pad token for T5Model.
    # This is not needed for torch's T5ForConditionalGeneration as it does this internally using labels arg.
    decoder_input_ids = hf_reference_model._shift_right(decoder_input_ids)

    with torch.no_grad():
        # PyTorch forward pass
        profiler.start(cpu_key)
        pt_out = hf_reference_model(
            input_ids=input_ids,
            decoder_input_ids=decoder_input_ids,
            attention_mask=attention_mask,
            decoder_attention_mask=decoder_attention_mask,
        )
        profiler.end(cpu_key)

        profiler.start(first_key)
        tt_model_outputs = tt_model(
            input_ids=input_ids,
            decoder_input_ids=decoder_input_ids,
            attention_mask=attention_mask,
            decoder_attention_mask=decoder_attention_mask,
        )
        tt_lib.device.Synchronize()
        profiler.end(first_key)
        del tt_model_outputs

        enable_persistent_kernel_cache()

        profiler.start(second_key)
        tt_model_outputs = tt_model(
            input_ids=input_ids,
            decoder_input_ids=decoder_input_ids,
            attention_mask=attention_mask,
            decoder_attention_mask=decoder_attention_mask,
        )
        tt_lib.device.Synchronize()
        profiler.end(second_key)
        del tt_model_outputs

    first_iter_time = profiler.get(first_key)
    second_iter_time = profiler.get(second_key)
    cpu_time = profiler.get(cpu_key)
    tt_lib.device.CloseDevice(device)
    compile_time = first_iter_time - second_iter_time

    prep_report("t5", 1, first_iter_time, second_iter_time, comments, cpu_time)
    logger.info(f"t5 small inference time: {second_iter_time}")
    logger.info(f"t5 compile time: {compile_time}")

    assert second_iter_time < expected_inference_time, f"t5 {comments} is too slow"
    assert compile_time < expected_compile_time, "t5 compile time is too slow"
