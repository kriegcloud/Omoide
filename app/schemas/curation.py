"""Closed input contracts: actor identities and filesystem paths are never inputs."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


SHA256 = Field(pattern=r'^[0-9a-f]{64}$')


class RevisionInput(Closed):
    expected_revision: int = Field(ge=0)


class ImageFrameSelector(Closed):
    """One explicitly requested frame/page of a multi-frame still."""
    kind: Literal['image_frame']
    index: int = Field(ge=0, le=4095)


class VideoFrameSelector(Closed):
    """One explicitly requested presentation timestamp of one video stream.

    The timestamp is an exact decimal string so that the request, the cache key
    and the recorded decoder lineage never depend on binary float rounding.
    """
    kind: Literal['video_frame']
    stream_index: int = Field(default=0, ge=0, le=15)
    pts_seconds: str = Field(pattern=r'^(0|[1-9][0-9]{0,4})(\.[0-9]{1,6})?$')


class MaskRegion(Closed):
    x: int = Field(ge=0, le=1_000_000)
    y: int = Field(ge=0, le=1_000_000)
    width: int = Field(ge=1, le=1_000_000)
    height: int = Field(ge=1, le=1_000_000)


class MaskEvidence(Closed):
    """Schema stub. No mask is produced, stored or accepted by this slice."""
    mask_artifact_sha256: str = SHA256
    mask_pixel_sha256: str = SHA256
    # Region boxes are meaningless without their coordinate space; a mask drawn
    # against pre-orientation source pixels cannot be reapplied to the artifact.
    coordinate_space: Literal['post_transform_artifact_pixels']
    space_width: int = Field(ge=1, le=1_000_000)
    space_height: int = Field(ge=1, le=1_000_000)
    regions: list[MaskRegion] = Field(min_length=1, max_length=64)


class GenerativeWorkflowEvidence(Closed):
    """Schema stub. Recording a workflow digest is not permission to run one."""
    runtime_identifier: str = Field(min_length=1, max_length=128)
    workflow_sha256: str = SHA256
    weights_sha256: str = SHA256
    prompt_sha256: str = SHA256
    negative_prompt_sha256: str = SHA256
    seed: int = Field(ge=0, le=2**63 - 1)
    strength: str = Field(pattern=r'^(0|1)(\.[0-9]{1,6})?$')


class ComparisonPairEvidence(Closed):
    """Schema stub: the before/after and unmasked comparison a reviewer needs."""
    before_artifact_sha256: str = SHA256
    after_artifact_sha256: str = SHA256
    unmasked_comparison_sha256: str = SHA256
    protected_detail_regions: list[MaskRegion] = Field(default_factory=list, max_length=64)


class RepairSelector(Closed):
    """Documented, validated shape of a repair/mask request that stays refused.

    Materializing this kind always fails with ``generative_disabled``: acceptance
    of the exact final bytes by a human is mandatory for generative output and no
    such acceptance exists in this slice.
    """
    kind: Literal['repair_evidence']
    operation: Literal['inpaint', 'person_removal', 'restoration', 'generative_upscale']
    mask: MaskEvidence
    workflow: GenerativeWorkflowEvidence
    comparison: ComparisonPairEvidence
    # Typed as None: a client can never assert human acceptance of exact bytes.
    human_acceptance_of_exact_output_sha256: None = None


FrameSelector = Annotated[ImageFrameSelector | VideoFrameSelector | RepairSelector,
                          Field(discriminator='kind')]
FRAME_SELECTOR = TypeAdapter(FrameSelector)


class MaterializeInput(RevisionInput):
    source_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r'^[a-zA-Z0-9_-]+$')
    # Absent means "the single frame of a single-frame still"; multi-frame and
    # video inputs are refused until one frame is explicitly requested here.
    frame: FrameSelector | None = None


class CaptionInput(RevisionInput):
    artifact_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=8192)
    # Optional so the browser keeps its exact behaviour; agents supply one so a
    # retried proposal replays the recorded operation instead of a new revision.
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128,
                                        pattern=r'^[a-zA-Z0-9_-]+$')


class PresenceInput(Closed):
    challenge_id: str = Field(min_length=1, max_length=64)
    credential: dict


class RegistrationInput(PresenceInput):
    pass


class EmptyInput(Closed):
    pass


class ReviewInput(RevisionInput):
    artifact_id: str = Field(min_length=1, max_length=64)
    caption_id: str = Field(min_length=1, max_length=64)
    asset_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    caption_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decision: Literal['accept', 'reject', 'defer']
    rationale: str = Field(default='', max_length=2048)
    presence: PresenceInput | None = None


class ExportInput(RevisionInput):
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r'^[a-zA-Z0-9_-]+$')
