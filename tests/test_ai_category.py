from sorter.classifier import classify, match_type
from sorter.config import Config


def make_config():
    return Config(
        downloads_path="X:/dummy",
        categories={
            "Нейросети": ["comfyui", "lm-studio", "sdxl", ".gguf", ".safetensors", ".ckpt"],
            "Программы": ["setup", "install"],
        },
        type_map={
            "Models": ["gguf", "ggml", "safetensors", "ckpt", "onnx", "pt", "pth", "lora", "sft"],
            "Installers": ["exe", "msi"],
        },
        managed_folders=["Нейросети", "Models", "Программы"],
    )


def test_gguf_is_ai_model():
    cfg = make_config()
    assert classify("qwen2.5-7b-instruct-q4.gguf", "", cfg) == ("Нейросети", "Models", "gguf")


def test_safetensors_is_ai_model():
    cfg = make_config()
    assert classify("sd_xl_base.safetensors", "", cfg) == ("Нейросети", "Models", "safetensors")


def test_comfyui_installer_goes_to_ai_not_programs():
    cfg = make_config()
    # "setup" подошло бы под Программы, но Нейросети раньше в списке
    assert classify("ComfyUI Setup 0.8.35 - x64.exe", "", cfg) == ("Нейросети", "Installers", "exe")


def test_model_type_from_extension():
    cfg = make_config()
    assert match_type("gguf", cfg.type_map) == "Models"


def test_plain_font_lora_is_not_ai():
    # бывший риск: шрифт "Lora" не должен попасть в Нейросети
    cfg = make_config()
    cat, _, _ = classify("Lora-Regular.ttf", "", cfg)
    assert cat != "Нейросети"
