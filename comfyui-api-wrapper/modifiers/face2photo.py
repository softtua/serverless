from modifiers.basemodifier import BaseModifier
import random
import time
import json


"""
Handler classes are generally bound to a specific workflow file.
To modify values we have to be confident in the json structure.
"""

class Face2Photo(BaseModifier):
    
    WORKFLOW_JSON = "workflows/face2photo.json"
    
    def __init__(self, modifications={}):
        super().__init__()
        self.modifications = modifications

    async def apply_modifications(self):
        timestr = time.strftime("%Y%m%d-%H%M%S")

        # Get mode to determine if fast mode is enabled
        mode = await self.modify_workflow_value("mode", "normal")

        # Handle seed
        self.workflow["65"]["inputs"]["seed"] = await self.modify_workflow_value(
            "seed",
            random.randint(0,2**32))

        # Handle steps - check if explicitly provided or use mode-based default
        if "steps" in self.modifications:
            # Steps explicitly provided, use it
            self.workflow["65"]["inputs"]["steps"] = await self.modify_workflow_value("steps", 8)
        else:
            # Steps not provided, use mode-based default
            if mode == "fast":
                self.workflow["65"]["inputs"]["steps"] = 5
            else:
                self.workflow["65"]["inputs"]["steps"] = 8

        self.workflow["65"]["inputs"]["sampler_name"] = await self.modify_workflow_value(
            "sampler_name",
            "euler")
        self.workflow["65"]["inputs"]["scheduler"] = await self.modify_workflow_value(
            "scheduler",
            "simple")


        self.workflow["66"]["inputs"]["batch_size"] = await self.modify_workflow_value("number_images", 1)
        self.workflow["66"]["inputs"]["width"] = await self.modify_workflow_value("width", 1024)
        self.workflow["66"]["inputs"]["height"] = await self.modify_workflow_value("height", 1024)

        self.workflow["68"]["inputs"]["prompt"] = await self.modify_workflow_value(
            "prompt",
            "territory orange style, portrait of the same person, directional lighting, the light falls at a 45-degree angle, creating a gentle highlight of the person's face and shoulders. The background is a dark gradient, emphasizing the subject. The person stands with arms crossed, looking slightly to the side, exuding confidence.")
        self.workflow["41"]["inputs"]["image"] = await self.modify_workflow_value(
            "input_image",
            "")

        # Handle face_strength (0-100, maps to 0.0-1.0)
        face_strength = await self.modify_workflow_value("face_strength", 70)
        self.workflow["71"]["inputs"]["strength_model"] = face_strength / 100.0

        # Handle mode - change LoRA for fast mode
        if mode == "fast":
            self.workflow["70"]["inputs"]["lora_name"] = "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"
        else:
            self.workflow["70"]["inputs"]["lora_name"] = "Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors"

        # Handle style - set LoRA strength for territory-orange style
        style = await self.modify_workflow_value("style", "territory-orange")
        if style != "territory-orange":
            self.workflow["72"]["inputs"]["strength_model"] = 0.0
        else:
            self.workflow["72"]["inputs"]["strength_model"] = 0.8

        await super().apply_modifications()
