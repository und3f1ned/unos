import os
import shutil
import stat
import pwd
import grp
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from fastapi.responses import FileResponse

from ..auth.utils import verify_token
from .schemas import FileInfo, FileType, DirectoryListing, CreateDirectoryRequest, RenameRequest

router = APIRouter(prefix="/files", tags=["files"])

BASE_PATH = "/mnt/pools"


def safe_path(path: str) -> Path:
    """Ensure path is within allowed base."""
    resolved = Path(path).resolve()
    base = Path(BASE_PATH).resolve()
    if not str(resolved).startswith(str(base)):
        raise HTTPException(403, "Access denied")
    return resolved


def get_file_info(path: Path) -> FileInfo:
    """Get file metadata."""
    stat_info = path.stat()
    
    if path.is_symlink():
        file_type = FileType.SYMLINK
    elif path.is_dir():
        file_type = FileType.DIRECTORY
    else:
        file_type = FileType.FILE
    
    try:
        owner = pwd.getpwuid(stat_info.st_uid).pw_name
    except KeyError:
        owner = str(stat_info.st_uid)
    
    try:
        group = grp.getgrgid(stat_info.st_gid).gr_name
    except KeyError:
        group = str(stat_info.st_gid)
    
    return FileInfo(
        name=path.name,
        path=str(path),
        type=file_type,
        size=stat_info.st_size if file_type == FileType.FILE else 0,
        modified=datetime.fromtimestamp(stat_info.st_mtime).isoformat(),
        permissions=stat.filemode(stat_info.st_mode),
        owner=owner,
        group=group,
    )


@router.get("/list", response_model=DirectoryListing)
async def list_directory(path: str = BASE_PATH, _: dict = Depends(verify_token)):
    """List directory contents."""
    dir_path = safe_path(path)
    
    if not dir_path.exists():
        raise HTTPException(404, f"Path not found: {path}")
    
    if not dir_path.is_dir():
        raise HTTPException(400, f"Not a directory: {path}")
    
    items = []
    total_size = 0
    
    for entry in sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        try:
            info = get_file_info(entry)
            items.append(info)
            total_size += info.size
        except PermissionError:
            continue
    
    parent = str(dir_path.parent) if dir_path != Path(BASE_PATH) else None
    
    return DirectoryListing(
        path=str(dir_path),
        parent=parent,
        items=items,
        total_size=total_size,
        item_count=len(items),
    )


@router.get("/info", response_model=FileInfo)
async def get_file(path: str, _: dict = Depends(verify_token)):
    """Get file or directory info."""
    file_path = safe_path(path)
    
    if not file_path.exists():
        raise HTTPException(404, f"Path not found: {path}")
    
    return get_file_info(file_path)


@router.get("/download")
async def download_file(path: str, _: dict = Depends(verify_token)):
    """Download a file."""
    file_path = safe_path(path)
    
    if not file_path.exists():
        raise HTTPException(404, f"File not found: {path}")
    
    if not file_path.is_file():
        raise HTTPException(400, "Can only download files")
    
    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="application/octet-stream",
    )


@router.post("/upload")
async def upload_file(
    path: str,
    file: UploadFile = File(...),
    _: dict = Depends(verify_token)
):
    """Upload a file."""
    dir_path = safe_path(path)
    
    if not dir_path.is_dir():
        raise HTTPException(400, "Destination must be a directory")
    
    file_path = dir_path / file.filename
    
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)
    
    return get_file_info(file_path)


@router.post("/mkdir", response_model=FileInfo)
async def create_directory(req: CreateDirectoryRequest, _: dict = Depends(verify_token)):
    """Create a new directory."""
    parent = safe_path(req.path)
    
    if not parent.is_dir():
        raise HTTPException(400, "Parent must be a directory")
    
    new_dir = parent / req.name
    
    if new_dir.exists():
        raise HTTPException(400, f"Already exists: {req.name}")
    
    new_dir.mkdir(parents=False)
    return get_file_info(new_dir)


@router.post("/rename", response_model=FileInfo)
async def rename_item(req: RenameRequest, _: dict = Depends(verify_token)):
    """Rename a file or directory."""
    source = safe_path(req.path)
    
    if not source.exists():
        raise HTTPException(404, f"Not found: {req.path}")
    
    dest = source.parent / req.new_name
    safe_path(str(dest))
    
    if dest.exists():
        raise HTTPException(400, f"Already exists: {req.new_name}")
    
    source.rename(dest)
    return get_file_info(dest)


@router.post("/copy")
async def copy_items(
    sources: list[str],
    destination: str,
    _: dict = Depends(verify_token)
):
    """Copy files/directories."""
    dest_path = safe_path(destination)
    
    if not dest_path.is_dir():
        raise HTTPException(400, "Destination must be a directory")
    
    copied = []
    for src in sources:
        src_path = safe_path(src)
        if not src_path.exists():
            continue
        
        dest_item = dest_path / src_path.name
        
        if src_path.is_dir():
            shutil.copytree(src_path, dest_item)
        else:
            shutil.copy2(src_path, dest_item)
        
        copied.append(str(dest_item))
    
    return {"copied": copied}


@router.post("/move")
async def move_items(
    sources: list[str],
    destination: str,
    _: dict = Depends(verify_token)
):
    """Move files/directories."""
    dest_path = safe_path(destination)
    
    if not dest_path.is_dir():
        raise HTTPException(400, "Destination must be a directory")
    
    moved = []
    for src in sources:
        src_path = safe_path(src)
        if not src_path.exists():
            continue
        
        dest_item = dest_path / src_path.name
        shutil.move(src_path, dest_item)
        moved.append(str(dest_item))
    
    return {"moved": moved}


@router.delete("/delete")
async def delete_items(paths: list[str], _: dict = Depends(verify_token)):
    """Delete files/directories."""
    deleted = []
    for path in paths:
        item = safe_path(path)
        if not item.exists():
            continue
        
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
        
        deleted.append(path)
    
    return {"deleted": deleted}
