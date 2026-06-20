# Категория «Нейросети» — дизайн

Дата: 2026-05-29

## Цель

Отдельная категория для файлов нейросетей (модели + инструменты ИИ),
обычная категория в загрузках со структурой Нейросети/Тип/файл.
Пройтись по текущим папкам и вытащить такие файлы в неё.

## config.json

- Новая категория «Нейросети» РАНЬШЕ Код и Программы (приоритет — первое совпадение):
  - инструменты: comfyui, lm-studio, lmstudio, sdxl, stable diffusion, stable-diffusion,
    automatic1111, a1111, fooocus, invokeai, kobold, ollama, oobabooga, exllama,
    controlnet, vae, checkpoint, diffusers, huggingface, whisper, openclip,
    mistral, qwen, gemma, flux, openclaw.
  - расширения (с точкой): .gguf, .ggml, .safetensors, .ckpt, .onnx, .pt, .pth, .lora, .sft.
  - НЕ берём: llama (Minecraft), lora без точки (шрифт Lora / flora).
- Новый тип Models в type_map: gguf, ggml, safetensors, ckpt, onnx, pt, pth, lora, sft.
- managed_folders += Нейросети, Models.
- Из категории Код убираем ключевые слова comfyui, lm-studio.

## overrides.json

- ComfyUI Setup...exe: Код -> Нейросети
- LM-Studio...exe: Код -> Нейросети
- OpenClaw-Setup-0.7.0.exe: -> Нейросети (добавить)

## Поведение

- Модель .gguf -> Нейросети/Models/файл.gguf
- Установщик ComfyUI/LM-Studio -> Нейросети/Installers/
- Выводы ComfyUI_00001_* -> Нейросети/Audio (или по типу)
- Повторный прогон по Код/Программы вытащит ИИ-файлы в Нейросети.

## Тесты (TDD)

- classify('model.gguf') -> ('Нейросети','Models','gguf')
- classify('ComfyUI Setup.exe') -> ('Нейросети','Installers','exe')
- match_type('gguf') -> 'Models'
