"""Data models (sqlite3, Phase 1)"""

from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class VM:
    """Virtual Machine data model"""

    id: str
    name: str
    hostname: str
    state: str
    cpu_count: int
    memory_gb: float
    cluster_name: Optional[str] = None
    client_id: Optional[int] = None

    @classmethod
    def from_row(cls, row) -> "VM":
        """Create VM from sqlite3 Row"""
        return cls(
            id=row["id"],
            name=row["name"],
            hostname=row["hostname"],
            state=row["state"],
            cpu_count=row["cpu_count"],
            memory_gb=row["memory_gb"],
            cluster_name=row.get("cluster_name"),
            client_id=row.get("client_id"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "id": self.id,
            "name": self.name,
            "hostname": self.hostname,
            "state": self.state,
            "cpu_count": self.cpu_count,
            "memory_gb": self.memory_gb,
            "cluster_name": self.cluster_name,
            "client_id": self.client_id,
        }


@dataclass
class Host:
    """Hyper-V Host data model"""

    hostname: str
    cluster_name: Optional[str]
    total_memory_gb: float
    available_memory_gb: float
    logical_processor_count: int

    @classmethod
    def from_row(cls, row) -> "Host":
        """Create Host from sqlite3 Row"""
        return cls(
            hostname=row["hostname"],
            cluster_name=row.get("cluster_name"),
            total_memory_gb=row["total_memory_gb"],
            available_memory_gb=row["available_memory_gb"],
            logical_processor_count=row["logical_processor_count"],
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "hostname": self.hostname,
            "cluster_name": self.cluster_name,
            "total_memory_gb": self.total_memory_gb,
            "available_memory_gb": self.available_memory_gb,
            "logical_processor_count": self.logical_processor_count,
        }
