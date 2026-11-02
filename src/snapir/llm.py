"""LLM configuration for topic-gen generators, read from the environment:

    SNAPIR_LLM_MODEL, SNAPIR_LLM_BASE_URL, SNAPIR_LLM_API_KEY (required)
    SNAPIR_LLM_TEMPERATURE (default 1), SNAPIR_LLM_CONCURRENCY (default 1)

Models whose name starts with "gemini" use Google GenAI (requires
langchain-google-genai), everything else an OpenAI-compatible endpoint.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from topic_gen.generate import Generator


def require_topic_gen() -> None:
    """Explain how to install the optional generation dependency."""
    try:
        import topic_gen  # noqa: F401
    except ModuleNotFoundError as exc:
        if exc.name != "topic_gen":
            raise
        raise ValueError(
            "LLM generation requires topic-gen. Install it with: "
            'pip install "topic-gen @ git+https://github.com/irgroup/topic-gen.git"'
        ) from None


def model_name() -> str:
    name = os.environ.get("SNAPIR_LLM_MODEL")
    if not name:
        raise ValueError("SNAPIR_LLM_MODEL must be set in environment variables.")
    return name


def make_generator(prompt: Path | str, output_class: type) -> Generator:
    """Generator for `prompt` (a YAML file or a topic-gen built-in prompt name)."""
    require_topic_gen()
    from topic_gen.generate import Generator

    name = model_name()
    base_url = os.environ.get("SNAPIR_LLM_BASE_URL")
    api_key = os.environ.get("SNAPIR_LLM_API_KEY")
    temperature = float(os.environ.get("SNAPIR_LLM_TEMPERATURE", 1))
    max_concurrency = int(os.environ.get("SNAPIR_LLM_CONCURRENCY", 1))

    if name.startswith("gemini"):
        from langchain_google_genai import ChatGoogleGenerativeAI

        llm = ChatGoogleGenerativeAI(model=name, temperature=temperature)
    else:
        if not base_url or not api_key:
            raise ValueError(
                "SNAPIR_LLM_BASE_URL and SNAPIR_LLM_API_KEY must be set in environment variables."
            )
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            openai_api_base=base_url,
            openai_api_key=api_key,
            model_name=name,
            temperature=temperature,
        )
    return Generator(
        llm=llm,
        output_class=output_class,
        prompt=prompt,
        config={"max_concurrency": max_concurrency},
    )


def prompt_name(generator: Generator) -> str:
    """Prompt name as recorded on generated records (without directories)."""
    return generator.prompt_name.split("/")[-1]
