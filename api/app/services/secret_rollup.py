"""
Service for secret rollup and deduplication with source provenance.
Implements B-025 - Secret Rollup by Type+Value with Source Provenance.
"""

from collections import defaultdict
from typing import Dict, List, Any, Set, Tuple
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)


@dataclass
class SecretOccurrence:
    """Represents a single occurrence of a secret in a file."""
    file_id: str
    file_url: str
    line: int | None
    column: int | None
    context: str | None
    extractor: str
    rule_id: str | None
    confidence: str | None


@dataclass
class RolledUpSecret:
    """Represents a secret rolled up across multiple files."""
    type: str
    value: str
    rule_id: str | None
    rule_name: str | None
    occurrence_count: int
    file_count: int
    extractors: List[str]
    confidence_levels: List[str]
    occurrences: List[SecretOccurrence]
    first_seen: str | None  # file_url where first found
    risk_score: float | None  # Calculated risk based on various factors


class SecretRollupService:
    """Service for aggregating and deduplicating secrets across session files."""
    
    def __init__(self):
        self.risk_weights = {
            "type": {
                "api_key": 0.9,
                "jwt": 0.8,
                "password": 0.7,
                "token": 0.8,
                "secret": 0.6,
                "private_key": 0.95,
                "certificate": 0.5,
                "connection_string": 0.85,
                "webhook": 0.4,
            },
            "confidence": {
                "high": 1.0,
                "medium": 0.7,
                "low": 0.4,
            },
            "extractor": {
                "rep_kingfisher": 0.9,  # More reliable
                "jsluice_secrets": 0.7,  # Less reliable
                "reconstructed": 0.8,
                "custom_patterns": 0.6,
            }
        }
    
    def rollup_secrets(self, file_analyses: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Roll up secrets across multiple file analyses.
        
        Args:
            file_analyses: List of file analysis records with analysis.secrets
            
        Returns:
            Dictionary with rolled up secrets and summary statistics
        """
        logger.info(f"Rolling up secrets across {len(file_analyses)} file analyses")
        
        # Group secrets by (type, value) key
        secret_groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
        
        for analysis in file_analyses:
            file_id = analysis.get("id")
            file_url = analysis.get("file", {}).get("url", "unknown")
            secrets = analysis.get("analysis", {}).get("secrets", [])
            
            for secret in secrets:
                secret_type = secret.get("type", "secret")
                secret_value = secret.get("value", "")
                
                if not secret_value.strip():
                    continue
                    
                # Normalize the key for grouping
                key = (secret_type.lower().strip(), secret_value.strip())
                
                # Enrich secret with file context
                enriched_secret = {
                    **secret,
                    "file_id": file_id,
                    "file_url": file_url
                }
                
                secret_groups[key].append(enriched_secret)
        
        # Convert groups to rolled up secrets
        rolled_up_secrets = []
        for (secret_type, secret_value), group in secret_groups.items():
            rolled_up = self._create_rolled_up_secret(secret_type, secret_value, group)
            rolled_up_secrets.append(rolled_up)
        
        # Sort by risk score (descending) and occurrence count
        rolled_up_secrets.sort(
            key=lambda s: (s.risk_score or 0, s.occurrence_count, s.file_count), 
            reverse=True
        )
        
        # Calculate summary statistics
        summary_stats = self._calculate_summary_stats(rolled_up_secrets)
        
        logger.info(f"Rolled up {len(rolled_up_secrets)} unique secrets from {len(file_analyses)} files")
        
        return {
            "secrets": [asdict(secret) for secret in rolled_up_secrets],
            "summary": summary_stats
        }
    
    def _create_rolled_up_secret(self, secret_type: str, secret_value: str, group: List[Dict[str, Any]]) -> RolledUpSecret:
        """Create a rolled up secret from a group of identical secrets."""
        
        # Collect all occurrences
        occurrences = []
        extractors = set()
        confidence_levels = set()
        file_ids = set()
        rule_ids = set()
        rule_names = set()
        
        for secret in group:
            file_id = secret.get("file_id")
            file_url = secret.get("file_url", "unknown")
            extractor = secret.get("extractor", "unknown")
            confidence = secret.get("confidence", "medium")
            rule_id = secret.get("ruleId") or secret.get("rule")
            rule_name = secret.get("ruleName")
            
            # Extract occurrence information
            line = secret.get("line")
            column = secret.get("column")
            context = secret.get("context", "")
            
            occurrence = SecretOccurrence(
                file_id=file_id,
                file_url=file_url,
                line=line,
                column=column,
                context=context,
                extractor=extractor,
                rule_id=rule_id,
                confidence=confidence
            )
            occurrences.append(occurrence)
            
            extractors.add(extractor)
            confidence_levels.add(confidence)
            file_ids.add(file_id)
            
            if rule_id:
                rule_ids.add(rule_id)
            if rule_name:
                rule_names.add(rule_name)
        
        # Calculate risk score
        risk_score = self._calculate_risk_score(
            secret_type, 
            list(extractors), 
            list(confidence_levels),
            len(file_ids),
            len(occurrences)
        )
        
        # Determine primary rule (most common one)
        primary_rule_id = max(rule_ids, key=lambda r: sum(1 for s in group if s.get("ruleId") == r or s.get("rule") == r)) if rule_ids else None
        primary_rule_name = max(rule_names, key=lambda r: sum(1 for s in group if s.get("ruleName") == r)) if rule_names else None
        
        return RolledUpSecret(
            type=secret_type,
            value=secret_value,
            rule_id=primary_rule_id,
            rule_name=primary_rule_name,
            occurrence_count=len(occurrences),
            file_count=len(file_ids),
            extractors=sorted(list(extractors)),
            confidence_levels=sorted(list(confidence_levels)),
            occurrences=occurrences,
            first_seen=occurrences[0].file_url if occurrences else None,
            risk_score=risk_score
        )
    
    def _calculate_risk_score(self, secret_type: str, extractors: List[str], confidences: List[str], file_count: int, occurrence_count: int) -> float:
        """Calculate a risk score for a secret based on various factors."""
        
        # Base score from secret type
        type_score = self.risk_weights["type"].get(secret_type.lower(), 0.5)
        
        # Confidence score (use highest confidence)
        conf_scores = [self.risk_weights["confidence"].get(c, 0.5) for c in confidences]
        confidence_score = max(conf_scores) if conf_scores else 0.5
        
        # Extractor score (use highest reliability)
        ext_scores = [self.risk_weights["extractor"].get(e, 0.5) for e in extractors]
        extractor_score = max(ext_scores) if ext_scores else 0.5
        
        # Frequency multiplier (more files = higher risk)
        frequency_multiplier = min(1 + (file_count - 1) * 0.1, 2.0)  # Cap at 2x
        
        # Occurrence multiplier (more occurrences = slightly higher risk)
        occurrence_multiplier = min(1 + (occurrence_count - 1) * 0.05, 1.5)  # Cap at 1.5x
        
        # Calculate final score
        base_score = (type_score * 0.5) + (confidence_score * 0.3) + (extractor_score * 0.2)
        final_score = base_score * frequency_multiplier * occurrence_multiplier
        
        return round(min(final_score, 1.0), 3)  # Cap at 1.0, round to 3 decimals
    
    def _calculate_summary_stats(self, rolled_up_secrets: List[RolledUpSecret]) -> Dict[str, Any]:
        """Calculate summary statistics for the rolled up secrets."""
        
        if not rolled_up_secrets:
            return {
                "total_unique_secrets": 0,
                "total_occurrences": 0,
                "total_files_with_secrets": 0,
                "by_type": {},
                "by_confidence": {},
                "by_extractor": {},
                "risk_distribution": {
                    "high_risk": 0,  # >= 0.8
                    "medium_risk": 0,  # 0.5 - 0.8
                    "low_risk": 0  # < 0.5
                }
            }
        
        total_occurrences = sum(s.occurrence_count for s in rolled_up_secrets)
        all_file_ids = set()
        for secret in rolled_up_secrets:
            all_file_ids.update(occ.file_id for occ in secret.occurrences)
        
        # Group by type
        by_type = defaultdict(int)
        for secret in rolled_up_secrets:
            by_type[secret.type] += 1
        
        # Group by confidence (use highest confidence per secret)
        by_confidence = defaultdict(int)
        for secret in rolled_up_secrets:
            highest_conf = "low"
            if "high" in secret.confidence_levels:
                highest_conf = "high"
            elif "medium" in secret.confidence_levels:
                highest_conf = "medium"
            by_confidence[highest_conf] += 1
        
        # Group by extractor
        by_extractor = defaultdict(int)
        for secret in rolled_up_secrets:
            for extractor in secret.extractors:
                by_extractor[extractor] += 1
        
        # Risk distribution
        risk_dist = {"high_risk": 0, "medium_risk": 0, "low_risk": 0}
        for secret in rolled_up_secrets:
            risk_score = secret.risk_score or 0
            if risk_score >= 0.8:
                risk_dist["high_risk"] += 1
            elif risk_score >= 0.5:
                risk_dist["medium_risk"] += 1
            else:
                risk_dist["low_risk"] += 1
        
        return {
            "total_unique_secrets": len(rolled_up_secrets),
            "total_occurrences": total_occurrences,
            "total_files_with_secrets": len(all_file_ids),
            "by_type": dict(by_type),
            "by_confidence": dict(by_confidence),
            "by_extractor": dict(by_extractor),
            "risk_distribution": risk_dist,
            "deduplication_ratio": round(total_occurrences / len(rolled_up_secrets), 2) if rolled_up_secrets else 0
        }