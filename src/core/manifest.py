import yaml
import logging
from typing import Dict, Any
from src.db.relational import SessionLocal, SkillRecord

log = logging.getLogger("vex.manifest")

class SkillManifestParser:
    def __init__(self, yaml_content: str):
        self.raw_data = yaml.safe_load(yaml_content) or {}
        self.skills = self.raw_data.get("skills", {})

    def resolve_inheritance(self) -> Dict[str, Any]:
        """Resolve the inheritance by combining the base skill with the child skill."""
        resolved_skills = {}
        
        for skill_id, config in self.skills.items():
            base_config = {}
            if "inherits" in config:
                parent_id = config["inherits"]
                if parent_id in self.skills:
                    base_config = self.skills[parent_id].copy()
                else:
                    log.warning(f"Skill '{skill_id}' inherits from unknown '{parent_id}'")
            
            base_config.update(config)
            base_config.pop("inherits", None) 
            
            resolved_skills[skill_id] = base_config
            
        return resolved_skills

    def auto_register_skills(self, tenant_id: str):
        """Automatically register the skills in SQLite that have auto_register: true."""
        resolved_skills = self.resolve_inheritance()
        db = SessionLocal()
        
        try:
            for skill_id, config in resolved_skills.items():
                if config.get("auto_register", False):
                    existing = db.query(SkillRecord).filter_by(tenant_id=tenant_id, skill_id=skill_id).first()
                    if not existing:
                        new_skill = SkillRecord(
                            tenant_id=tenant_id,
                            skill_id=skill_id,
                            name=skill_id,
                            description=config.get("description", "No description provided.")
                        )
                        db.add(new_skill)
                        log.info(f"Auto-registered skill '{skill_id}' for tenant '{tenant_id}'")
            db.commit()
        except Exception as e:
            db.rollback()
            log.error(f"Error auto-registering skills: {e}")
        finally:
            db.close()
            
        return resolved_skills