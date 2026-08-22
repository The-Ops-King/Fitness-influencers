"""Source modules. Each writes to the same staging schema and runs independently."""

from .base import MODULE_REGISTRY, ModuleContext, SourceModule, get_module, run_module
from .m1_booking_serp import BookingSerpModule
from .m2_meta_ads import MetaAdsModule
from .m3_instagram import InstagramModule
from .m4_youtube import YouTubeModule
from .m5_directories import DirectoryModule
from .m6_testimonials import TestimonialModule

__all__ = [
    "MODULE_REGISTRY",
    "ModuleContext",
    "SourceModule",
    "get_module",
    "run_module",
    "BookingSerpModule",
    "MetaAdsModule",
    "InstagramModule",
    "YouTubeModule",
    "DirectoryModule",
    "TestimonialModule",
]
