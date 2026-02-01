from modifiers.basemodifier import BaseModifier
import random
import time
import json


"""
Handler classes are generally bound to a specific workflow file.
To modify values we have to be confident in the json structure.
"""

class Image2Image(BaseModifier):
    
    WORKFLOW_JSON = "workflows/face2photo.json"
    
    def __init__(self, modifications={}):
        super().__init__()
        self.modifications = modifications

    async def apply_modifications(self):
        timestr = time.strftime("%Y%m%d-%H%M%S")
        self.workflow["65"]["inputs"]["seed"] = await self.modify_workflow_value(
            "seed",
            random.randint(0,2**32))
        self.workflow["65"]["inputs"]["steps"] = await self.modify_workflow_value(
            "steps",
            8)
        self.workflow["65"]["inputs"]["sampler_name"] = await self.modify_workflow_value(
            "sampler_name",
            "euler")
        self.workflow["65"]["inputs"]["scheduler"] = await self.modify_workflow_value(
            "scheduler",
            "simple")
        
        self.workflow["68"]["inputs"]["prompt"] = await self.modify_workflow_value(
            "prompt",
            "a studio portrait with a dramatic lighting style, The light is soft and directional, casting a warm orange glow on the woman's face, The light falls at an angle slightly above and to the right of the woman, creating a gentle shadow on the left side, The woman has has long golden hair and is standing with hands clasped in front, looking slightly to the left, The background is a dark gradient, emphasizing the subject")
        self.workflow["41"]["inputs"]["image"] = await self.modify_workflow_value(
            "input_image",
            "")
        await super().apply_modifications()
