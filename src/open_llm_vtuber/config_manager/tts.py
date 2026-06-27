# config_manager/tts.py
from pydantic import ValidationInfo, Field, model_validator
from typing import Literal, Optional, Dict, ClassVar
from .i18n import I18nMixin, Description

CartesiaLanguages = Literal[
    "en",
    "fr",
    "de",
    "es",
    "pt",
    "zh",
    "ja",
    "hi",
    "it",
    "ko",
    "nl",
    "pl",
    "ru",
    "sv",
    "tr",
    "tl",
    "bg",
    "ro",
    "ar",
    "cs",
    "el",
    "fi",
    "hr",
    "ms",
    "sk",
    "da",
    "ta",
    "uk",
    "hu",
    "no",
    "vi",
    "bn",
    "th",
    "he",
    "ka",
    "id",
    "te",
    "gu",
    "kn",
    "ml",
    "mr",
    "pa",
]

CartesiaEmotions = Literal[
    "neutral",
    "angry",
    "excited",
    "content",
    "sad",
    "scared",
    "happy",
    "enthusiastic",
    "elated",
    "euphoric",
    "triumphant",
    "amazed",
    "surprised",
    "flirtatious",
    "joking/comedic",
    "curious",
    "peaceful",
    "serene",
    "calm",
    "grateful",
    "affectionate",
    "trust",
    "sympathetic",
    "anticipation",
    "mysterious",
    "mad",
    "outraged",
    "frustrated",
    "agitated",
    "threatened",
    "disgusted",
    "contempt",
    "envious",
    "sarcastic",
    "ironic",
    "dejected",
    "melancholic",
    "disappointed",
    "hurt",
    "guilty",
    "bored",
    "tired",
    "rejected",
    "nostalgic",
    "wistful",
    "apologetic",
    "hesitant",
    "insecure",
    "confused",
    "resigned",
    "anxious",
    "panicked",
    "alarmed",
    "proud",
    "confident",
    "distant",
    "skeptical",
    "contemplative",
    "determined",
]


class AzureTTSConfig(I18nMixin):
    """Configuration for Azure TTS service."""

    api_key: str = Field(..., alias="api_key")
    region: str = Field(..., alias="region")
    voice: str = Field(..., alias="voice")
    pitch: str = Field(..., alias="pitch")
    rate: str = Field(..., alias="rate")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "api_key": Description(
            en="API key for Azure TTS service"
        ),
        "region": Description(
            en="Azure region (e.g., eastus)"
        ),
        "voice": Description(
            en="Voice name to use for Azure TTS"
        ),
        "pitch": Description(en="Pitch adjustment percentage"),
        "rate": Description(en="Speaking rate adjustment"),
    }


class BarkTTSConfig(I18nMixin):
    """Configuration for Bark TTS."""

    voice: str = Field(..., alias="voice")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "voice": Description(
            en="Voice name to use for Bark TTS"
        ),
    }


class EdgeTTSConfig(I18nMixin):
    """Configuration for Edge TTS."""

    voice: str = Field(..., alias="voice")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "voice": Description(
            en="Voice name to use for Edge TTS (use 'edge-tts --list-voices' to list available voices)",
        ),
    }


class CosyvoiceTTSConfig(I18nMixin):
    """Configuration for Cosyvoice TTS."""

    client_url: str = Field(..., alias="client_url")
    mode_checkbox_group: str = Field(..., alias="mode_checkbox_group")
    sft_dropdown: str = Field(..., alias="sft_dropdown")
    prompt_text: str = Field(..., alias="prompt_text")
    prompt_wav_upload_url: str = Field(..., alias="prompt_wav_upload_url")
    prompt_wav_record_url: str = Field(..., alias="prompt_wav_record_url")
    instruct_text: str = Field(..., alias="instruct_text")
    seed: int = Field(..., alias="seed")
    api_name: str = Field(..., alias="api_name")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "client_url": Description(
            en="URL of the CosyVoice Gradio web UI"
        ),
        "mode_checkbox_group": Description(
            en="Mode checkbox group value"
        ),
        "sft_dropdown": Description(en="SFT dropdown value"),
        "prompt_text": Description(en="Prompt text"),
        "prompt_wav_upload_url": Description(
            en="URL for prompt WAV file upload"
        ),
        "prompt_wav_record_url": Description(
            en="URL for prompt WAV file recording"
        ),
        "instruct_text": Description(en="Instruction text"),
        "seed": Description(en="Random seed"),
        "api_name": Description(en="API endpoint name"),
    }


class Cosyvoice2TTSConfig(I18nMixin):
    """Configuration for Cosyvoice2 TTS."""

    client_url: str = Field(..., alias="client_url")
    mode_checkbox_group: str = Field(..., alias="mode_checkbox_group")
    sft_dropdown: str = Field(..., alias="sft_dropdown")
    prompt_text: str = Field(..., alias="prompt_text")
    prompt_wav_upload_url: str = Field(..., alias="prompt_wav_upload_url")
    prompt_wav_record_url: str = Field(..., alias="prompt_wav_record_url")
    instruct_text: str = Field(..., alias="instruct_text")
    stream: bool = Field(..., alias="stream")
    seed: int = Field(..., alias="seed")
    speed: float = Field(..., alias="speed")
    api_name: str = Field(..., alias="api_name")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "client_url": Description(
            en="URL of the CosyVoice Gradio web UI"
        ),
        "mode_checkbox_group": Description(
            en="Mode checkbox group value"
        ),
        "sft_dropdown": Description(en="SFT dropdown value"),
        "prompt_text": Description(en="Prompt text"),
        "prompt_wav_upload_url": Description(
            en="URL for prompt WAV file upload"
        ),
        "prompt_wav_record_url": Description(
            en="URL for prompt WAV file recording"
        ),
        "instruct_text": Description(en="Instruction text"),
        "stream": Description(en="Streaming inference"),
        "seed": Description(en="Random seed"),
        "speed": Description(en="Speech speed multiplier"),
        "api_name": Description(en="API endpoint name"),
    }


class MeloTTSConfig(I18nMixin):
    """Configuration for Melo TTS."""

    speaker: str = Field(..., alias="speaker")
    language: str = Field(..., alias="language")
    device: str = Field("auto", alias="device")
    speed: float = Field(1.0, alias="speed")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "speaker": Description(
            en="Speaker name (e.g., EN-Default, ZH)",
        ),
        "language": Description(
            en="Language code (e.g., EN, ZH)"
        ),
        "device": Description(
            en="Device to use (auto, cpu, cuda, cuda:0, mps)",
        ),
        "speed": Description(en="Speech speed multiplier"),
    }


class XTTSConfig(I18nMixin):
    """Configuration for XTTS."""

    api_url: str = Field(..., alias="api_url")
    speaker_wav: str = Field(..., alias="speaker_wav")
    language: str = Field(..., alias="language")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "api_url": Description(
            en="URL of the XTTS API endpoint"
        ),
        "speaker_wav": Description(
            en="Speaker reference WAV file"
        ),
        "language": Description(
            en="Language code (e.g., en, zh)"
        ),
    }


class GPTSoVITSConfig(I18nMixin):
    """Configuration for GPT-SoVITS."""

    api_url: str = Field(..., alias="api_url")
    text_lang: str = Field(..., alias="text_lang")
    ref_audio_path: str = Field(..., alias="ref_audio_path")
    prompt_lang: str = Field(..., alias="prompt_lang")
    prompt_text: str = Field(..., alias="prompt_text")
    text_split_method: str = Field(..., alias="text_split_method")
    batch_size: str = Field(..., alias="batch_size")
    media_type: str = Field(..., alias="media_type")
    streaming_mode: str = Field(..., alias="streaming_mode")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "api_url": Description(
            en="URL of the GPT-SoVITS API endpoint"
        ),
        "text_lang": Description(en="Language of the input text"),
        "ref_audio_path": Description(
            en="Path to reference audio file"
        ),
        "prompt_lang": Description(en="Language of the prompt"),
        "prompt_text": Description(en="Prompt text"),
        "text_split_method": Description(
            en="Method for splitting text"
        ),
        "batch_size": Description(en="Batch size for processing"),
        "media_type": Description(en="Output media type"),
        "streaming_mode": Description(en="Enable streaming mode"),
    }


class FishAPITTSConfig(I18nMixin):
    """Configuration for Fish API TTS."""

    api_key: str = Field(..., alias="api_key")
    reference_id: str = Field(..., alias="reference_id")
    latency: Literal["normal", "balanced"] = Field(..., alias="latency")
    base_url: str = Field(..., alias="base_url")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "api_key": Description(
            en="API key for Fish TTS service"
        ),
        "reference_id": Description(
            en="Voice reference ID from Fish Audio website",
        ),
        "latency": Description(
            en="Latency mode (normal or balanced)"
        ),
        "base_url": Description(
            en="Base URL for Fish TTS API"
        ),
    }


class CoquiTTSConfig(I18nMixin):
    """Configuration for Coqui TTS."""

    model_name: str = Field(..., alias="model_name")
    speaker_wav: str = Field("", alias="speaker_wav")
    language: str = Field(..., alias="language")
    device: str = Field("", alias="device")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "model_name": Description(
            en="Name of the TTS model to use"
        ),
        "speaker_wav": Description(
            en="Path to speaker WAV file for voice cloning",
        ),
        "language": Description(
            en="Language code (e.g., en, zh)"
        ),
        "device": Description(
            en="Device to use (cuda, cpu, or empty for auto)",
        ),
    }


class SherpaOnnxTTSConfig(I18nMixin):
    """Configuration for Sherpa Onnx TTS."""

    vits_model: str = Field(..., alias="vits_model")
    vits_lexicon: Optional[str] = Field(None, alias="vits_lexicon")
    vits_tokens: str = Field(..., alias="vits_tokens")
    vits_data_dir: Optional[str] = Field(None, alias="vits_data_dir")
    vits_dict_dir: Optional[str] = Field(None, alias="vits_dict_dir")
    tts_rule_fsts: Optional[str] = Field(None, alias="tts_rule_fsts")
    max_num_sentences: int = Field(2, alias="max_num_sentences")
    sid: int = Field(1, alias="sid")
    provider: Literal["cpu", "cuda", "coreml"] = Field("cpu", alias="provider")
    num_threads: int = Field(1, alias="num_threads")
    speed: float = Field(1.0, alias="speed")
    debug: bool = Field(False, alias="debug")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "vits_model": Description(en="Path to VITS model file"),
        "vits_lexicon": Description(
            en="Path to lexicon file (optional)"
        ),
        "vits_tokens": Description(en="Path to tokens file"),
        "vits_data_dir": Description(
            en="Path to espeak-ng data directory (optional)",
        ),
        "vits_dict_dir": Description(
            en="Path to Jieba dictionary directory (optional)",
        ),
        "tts_rule_fsts": Description(
            en="Path to rule FSTs file (optional)"
        ),
        "max_num_sentences": Description(
            en="Maximum number of sentences per batch"
        ),
        "sid": Description(
            en="Speaker ID for multi-speaker models"
        ),
        "provider": Description(
            en="Computation provider (cpu, cuda, or coreml)",
        ),
        "num_threads": Description(en="Number of computation threads"),
        "speed": Description(en="Speech speed multiplier"),
        "debug": Description(en="Enable debug mode"),
    }


class SiliconFlowTTSConfig(I18nMixin):
    """Configuration for SiliconFlow TTS."""

    api_url: str = Field("https://api.siliconflow.cn/v1/audio/speech", alias="api_url")
    api_key: str = Field(..., alias="api_key")
    default_model: str = Field("FunAudioLLM/CosyVoice2-0.5B", alias="default_model")
    default_voice: str = Field(
        "speech:Dreamflowers:5bdstvc39i:xkqldnpasqmoqbakubom", alias="default_voice"
    )
    sample_rate: int = Field(32000, alias="sample_rate")
    response_format: str = Field("mp3", alias="response_format")
    stream: bool = Field(True, alias="stream")
    speed: float = Field(1, alias="speed")
    gain: int = Field(0, alias="gain")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "api_key": Description(
            en="API key for SiliconFlow TTS service",
        ),
        "url": Description(
            en="API endpoint URL for SiliconFlow TTS",
        ),
        "model": Description(
            en="Model to use for SiliconFlow TTS"
        ),
        "voice": Description(
            en="Voice name to use for SiliconFlow TTS",
        ),
        "sample_rate": Description(
            en="Sample rate of the output audio"
        ),
        "stream": Description(en="Enable streaming mode"),
        "speed": Description(en="Speaking speed multiplier"),
        "gain": Description(en="Audio gain adjustment"),
    }


class OpenAITTSConfig(I18nMixin):
    """Configuration for OpenAI-compatible TTS client."""

    model: Optional[str] = Field(None, alias="model")
    voice: Optional[str] = Field(None, alias="voice")
    api_key: Optional[str] = Field(None, alias="api_key")
    base_url: Optional[str] = Field(None, alias="base_url")
    file_extension: Literal["mp3", "wav"] = Field("mp3", alias="file_extension")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "model": Description(
            en="Model name for the TTS server (overrides default)",
        ),
        "voice": Description(
            en="Voice name(s) for the TTS server (overrides default)",
        ),
        "api_key": Description(
            en="API key if required by the TTS server (overrides default)",
        ),
        "base_url": Description(
            en="Base URL of the TTS server (overrides default)",
        ),
        "file_extension": Description(
            en="Audio file format (mp3 or wav, defaults to mp3)",
        ),
    }


class SparkTTSConfig(I18nMixin):
    """Configuration for Spark TTS."""

    api_url: str = Field(..., alias="api_url")
    prompt_wav_upload: str = Field(..., alias="prompt_wav_upload")
    api_name: str = Field(..., alias="api_name")
    gender: str = Field(..., alias="gender")
    pitch: int = Field(..., alias="pitch")
    speed: int = Field(..., alias="speed")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "prompt_wav_upload": Description(
            en="Reference audio (used when using voice cloning)",
        ),
        "api_url": Description(
            en="API address of the spark tts gradio web frontend. For example: http://127.0.0.1:7860/voice_clone",
        ),
        "api_name": Description(
            en="The API endpoint name. For example: voice_clone,voice_creation",
        ),
        "gender": Description(
            en="Gender of the voice (male or female)"
        ),
        "pitch": Description(
            en="Pitch shift (in semitones) default 3,range 1-5.",
        ),
        "speed": Description(
            en="Speed of the voice (in percent) default 3,range 1-5.",
        ),
    }


class MinimaxTTSConfig(I18nMixin):
    """Configuration for Minimax TTS."""

    group_id: str = Field(..., alias="group_id")
    api_key: str = Field(..., alias="api_key")
    model: str = Field("speech-02-turbo", alias="model")
    voice_id: str = Field("male-qn-qingse", alias="voice_id")
    pronunciation_dict: str = Field("", alias="pronunciation_dict")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "group_id": Description(en="Minimax group_id"),
        "api_key": Description(en="Minimax API key"),
        "model": Description(en="Minimax model name"),
        "voice_id": Description(en="Minimax voice id"),
        "pronunciation_dict": Description(
            en="Custom pronunciation dictionary (string)"
        ),
    }


class PiperTTSConfig(I18nMixin):
    """Configuration for Piper TTS."""

    model_path: str = Field("models/piper/zh_CN-huayan-medium.onnx", alias="model_path")
    speaker_id: int = Field(0, alias="speaker_id")
    length_scale: float = Field(1.0, alias="length_scale")
    noise_scale: float = Field(0.667, alias="noise_scale")
    noise_w: float = Field(0.8, alias="noise_w")
    volume: float = Field(1.0, alias="volume")
    normalize_audio: bool = Field(True, alias="normalize_audio")
    use_cuda: bool = Field(False, alias="use_cuda")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "model_path": Description(
            en="Path to Piper ONNX model file",
        ),
        "speaker_id": Description(
            en="Speaker ID for multi-speaker models (default 0 for single-speaker)",
        ),
        "length_scale": Description(
            en="Speech speed control (0.5=2x faster, 1.0=normal, 2.0=2x slower)",
        ),
        "noise_scale": Description(
            en="Audio variation degree (0.0-1.0, higher=more varied)",
        ),
        "noise_w": Description(
            en="Speaking style variation (0.0-1.0, higher=more diverse)",
        ),
        "volume": Description(
            en="Output volume (0.0-1.0, 1.0=normal)"
        ),
        "normalize_audio": Description(
            en="Whether to normalize audio output (recommended)",
        ),
        "use_cuda": Description(
            en="Whether to use GPU acceleration (requires onnxruntime-gpu)",
        ),
    }


class ElevenLabsTTSConfig(I18nMixin):
    """Configuration for ElevenLabs TTS."""

    api_key: str = Field(..., alias="api_key")
    voice_id: str = Field(..., alias="voice_id")
    model_id: str = Field("eleven_multilingual_v2", alias="model_id")
    output_format: str = Field("mp3_44100_128", alias="output_format")
    stability: float = Field(0.5, alias="stability")
    similarity_boost: float = Field(0.5, alias="similarity_boost")
    style: float = Field(0.0, alias="style")
    use_speaker_boost: bool = Field(True, alias="use_speaker_boost")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "api_key": Description(
            en="API key for ElevenLabs TTS service"
        ),
        "voice_id": Description(
            en="Voice ID from ElevenLabs (e.g., JBFqnCBsd6RMkjVDRZzb)",
        ),
        "model_id": Description(
            en="Model ID for ElevenLabs (e.g., eleven_multilingual_v2)",
        ),
        "output_format": Description(
            en="Output audio format (e.g., mp3_44100_128)",
        ),
        "stability": Description(
            en="Voice stability (0.0 to 1.0)"
        ),
        "similarity_boost": Description(
            en="Voice similarity boost (0.0 to 1.0)"
        ),
        "style": Description(
            en="Voice style exaggeration (0.0 to 1.0)",
        ),
        "use_speaker_boost": Description(
            en="Enable speaker boost for better quality",
        ),
    }


class CartesiaTTSConfig(I18nMixin):
    """Configuration for Cartesia TTS."""

    model_id: Literal[
        "sonic-3", "sonic-2", "sonic-turbo", "sonic-multilingual", "sonic"
    ] = Field("sonic-3", alias="model_id")

    api_key: str = Field(..., alias="api_key")
    voice_id: str = Field(..., alias="voice_id")
    output_format: Literal["wav", "mp3"] = Field("wav", alias="output_format")
    language: CartesiaLanguages = Field("en", alias="language")
    emotion: CartesiaEmotions = Field("neutral", alias="emotion")
    volume: float = Field(1.0, alias="volume")
    speed: float = Field(1.0, alias="speed")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "api_key": Description(
            en="API key for Cartesia TTS service"
        ),
        "voice_id": Description(
            en="Voice ID from Cartesia (e.g., 6ccbfb76-1fc6-48f7-b71d-91ac6298247b)",
        ),
        "model_id": Description(
            en="Model ID for Cartesia (e.g., sonic-3)",
        ),
        "output_format": Description(
            en="Output audio format (e.g., wav)",
        ),
        "language": Description(
            en="The language that the given voice should speak (e.g., en)",
        ),
        "emotion": Description(
            en="Emotional guidance for a generation (e.g., neutral)",
        ),
        "volume": Description(
            en="volume of the generation, ranging from 0.5 to 2.0 (e.g., 1)",
        ),
        "speed": Description(
            en="Speed of the generation, ranging from 0.6 to 1.5 (e.g., 1)",
        ),
    }


class TTSConfig(I18nMixin):
    """Configuration for Text-to-Speech."""

    tts_model: Literal[
        "azure_tts",
        "bark_tts",
        "edge_tts",
        "cosyvoice_tts",
        "cosyvoice2_tts",
        "melo_tts",
        "coqui_tts",
        "x_tts",
        "gpt_sovits_tts",
        "fish_api_tts",
        "sherpa_onnx_tts",
        "siliconflow_tts",
        "openai_tts",  # Add openai_tts here
        "spark_tts",
        "minimax_tts",
        "elevenlabs_tts",
        "cartesia_tts",
        "piper_tts",
    ] = Field(..., alias="tts_model")

    azure_tts: Optional[AzureTTSConfig] = Field(None, alias="azure_tts")
    bark_tts: Optional[BarkTTSConfig] = Field(None, alias="bark_tts")
    edge_tts: Optional[EdgeTTSConfig] = Field(None, alias="edge_tts")
    cosyvoice_tts: Optional[CosyvoiceTTSConfig] = Field(None, alias="cosyvoice_tts")
    cosyvoice2_tts: Optional[Cosyvoice2TTSConfig] = Field(None, alias="cosyvoice2_tts")
    melo_tts: Optional[MeloTTSConfig] = Field(None, alias="melo_tts")
    coqui_tts: Optional[CoquiTTSConfig] = Field(None, alias="coqui_tts")
    x_tts: Optional[XTTSConfig] = Field(None, alias="x_tts")
    gpt_sovits_tts: Optional[GPTSoVITSConfig] = Field(None, alias="gpt_sovits")
    fish_api_tts: Optional[FishAPITTSConfig] = Field(None, alias="fish_api_tts")
    sherpa_onnx_tts: Optional[SherpaOnnxTTSConfig] = Field(
        None, alias="sherpa_onnx_tts"
    )
    siliconflow_tts: Optional[SiliconFlowTTSConfig] = Field(
        None, alias="siliconflow_tts"
    )
    openai_tts: Optional[OpenAITTSConfig] = Field(None, alias="openai_tts")
    spark_tts: Optional[SparkTTSConfig] = Field(None, alias="spark_tts")
    minimax_tts: Optional[MinimaxTTSConfig] = Field(None, alias="minimax_tts")
    elevenlabs_tts: ElevenLabsTTSConfig | None = Field(None, alias="elevenlabs_tts")
    cartesia_tts: CartesiaTTSConfig | None = Field(None, alias="cartesia_tts")
    piper_tts: Optional[PiperTTSConfig] = Field(None, alias="piper_tts")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "tts_model": Description(
            en="Text-to-speech model to use"
        ),
        "azure_tts": Description(en="Configuration for Azure TTS"),
        "bark_tts": Description(en="Configuration for Bark TTS"),
        "edge_tts": Description(en="Configuration for Edge TTS"),
        "cosyvoice_tts": Description(
            en="Configuration for Cosyvoice TTS"
        ),
        "cosyvoice2_tts": Description(
            en="Configuration for Cosyvoice2 TTS"
        ),
        "melo_tts": Description(en="Configuration for Melo TTS"),
        "coqui_tts": Description(en="Configuration for Coqui TTS"),
        "x_tts": Description(en="Configuration for XTTS"),
        "gpt_sovits_tts": Description(
            en="Configuration for GPT-SoVITS"
        ),
        "fish_api_tts": Description(
            en="Configuration for Fish API TTS"
        ),
        "sherpa_onnx_tts": Description(
            en="Configuration for Sherpa Onnx TTS"
        ),
        "siliconflow_tts": Description(
            en="Configuration for SiliconFlow TTS"
        ),
        "openai_tts": Description(
            en="Configuration for OpenAI-compatible TTS"
        ),
        "spark_tts": Description(en="Configuration for Spark TTS"),
        "minimax_tts": Description(
            en="Configuration for Minimax TTS"
        ),
        "elevenlabs_tts": Description(
            en="Configuration for ElevenLabs TTS"
        ),
        "cartesia_tts": Description(
            en="Configuration for Cartesia TTS"
        ),
        "piper_tts": Description(en="Configuration for Piper TTS"),
    }

    @model_validator(mode="after")
    def check_tts_config(cls, values: "TTSConfig", info: ValidationInfo):
        tts_model = values.tts_model

        # Only validate the selected TTS model
        if tts_model == "azure_tts" and values.azure_tts is not None:
            values.azure_tts.model_validate(values.azure_tts.model_dump())
        elif tts_model == "bark_tts" and values.bark_tts is not None:
            values.bark_tts.model_validate(values.bark_tts.model_dump())
        elif tts_model == "edge_tts" and values.edge_tts is not None:
            values.edge_tts.model_validate(values.edge_tts.model_dump())
        elif tts_model == "cosyvoice_tts" and values.cosyvoice_tts is not None:
            values.cosyvoice_tts.model_validate(values.cosyvoice_tts.model_dump())
        elif tts_model == "cosyvoice2_tts" and values.cosyvoice2_tts is not None:
            values.cosyvoice2_tts.model_validate(values.cosyvoice2_tts.model_dump())
        elif tts_model == "melo_tts" and values.melo_tts is not None:
            values.melo_tts.model_validate(values.melo_tts.model_dump())
        elif tts_model == "coqui_tts" and values.coqui_tts is not None:
            values.coqui_tts.model_validate(values.coqui_tts.model_dump())
        elif tts_model == "x_tts" and values.x_tts is not None:
            values.x_tts.model_validate(values.x_tts.model_dump())
        elif tts_model == "gpt_sovits_tts" and values.gpt_sovits_tts is not None:
            values.gpt_sovits_tts.model_validate(values.gpt_sovits_tts.model_dump())
        elif tts_model == "fish_api_tts" and values.fish_api_tts is not None:
            values.fish_api_tts.model_validate(values.fish_api_tts.model_dump())
        elif tts_model == "sherpa_onnx_tts" and values.sherpa_onnx_tts is not None:
            values.sherpa_onnx_tts.model_validate(values.sherpa_onnx_tts.model_dump())
        elif tts_model == "siliconflow_tts" and values.siliconflow_tts is not None:
            values.siliconflow_tts.model_validate(values.siliconflow_tts.model_dump())
        elif tts_model == "openai_tts" and values.openai_tts is not None:
            values.openai_tts.model_validate(values.openai_tts.model_dump())
        elif tts_model == "spark_tts" and values.spark_tts is not None:
            values.spark_tts.model_validate(values.spark_tts.model_dump())
        elif tts_model == "minimax_tts" and values.minimax_tts is not None:
            values.minimax_tts.model_validate(values.minimax_tts.model_dump())
        elif tts_model == "elevenlabs_tts" and values.elevenlabs_tts is not None:
            values.elevenlabs_tts.model_validate(values.elevenlabs_tts.model_dump())
        elif tts_model == "cartesia_tts" and values.cartesia_tts is not None:
            values.cartesia_tts.model_validate(values.cartesia_tts.model_dump())

        elif tts_model == "piper_tts" and values.piper_tts is not None:
            values.piper_tts.model_validate(values.piper_tts.model_dump())
        return values
