import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

CONFIG_FILE = Path(__file__).with_name("config.toml")


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EmbeddingConfig(_Config):
    model: str = Field(min_length=1)
    dimension: PositiveInt
    batch_size: PositiveInt
    prefix: str = ""


class ClusteringConfig(_Config):
    umap_neighbors: int = Field(ge=2)
    umap_components: int = Field(ge=2)
    umap_metric: str
    min_cluster_size: int = Field(ge=2)
    min_samples: PositiveInt
    representatives: PositiveInt
    min_language_group: PositiveInt


class MlConfig(_Config):
    embeddings: EmbeddingConfig
    clustering: ClusteringConfig


def load_config(path: Path = CONFIG_FILE) -> MlConfig:
    return MlConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
