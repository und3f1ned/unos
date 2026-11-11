"""Limits on request bodies, and uploads read after the credentials are checked.

FastAPI reads and parses the body of a request (request.body() or
request.form()) before it runs the dependencies that check the credentials
(verify_token, require_admin). Without a limit, any client could make the
supervisor hold a body of any size in memory, or store an upload of any size,
before it is turned away with 401.

* RequestBodyLimitMiddleware limits the body of every HTTP request to
  MAX_REQUEST_BODY, except on the paths main.py gives their own limit (the
  uploads). No JSON body of this API comes near 1 MiB: the largest are a
  certificate with its key and chain, and the path lists of copy, move and
  delete in the file manager.
* Upload endpoints take the request itself instead of an UploadFile
  parameter, so FastAPI reads nothing before the dependencies have run, and
  stream their file with receive_upload() straight into its destination
  file (an UploadSink) as it arrives. They do not use request.form():
  Starlette spools every file over 1 MiB of a form to a temporary file in
  /tmp first (the system disk of the container, the SD card of a small
  board, or RAM on a tmpfs), so every upload was written twice, and one
  larger than the space in /tmp failed after filling it.
* receive_upload() waits at most UPLOAD_IDLE_TIMEOUT seconds for each chunk
  of an upload's body, then answers 408 and discards the upload: neither
  uvicorn nor Caddy limits the time a request body takes, so a client that
  stopped sending held the request and the upload's destination file, with
  the space of what had arrived, for as long as its connection stayed open.
"""

import asyncio
import codecs
import logging
import os
import threading
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import aclosing
from typing import Any

import python_multipart
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MAX_BOUNDARY_LENGTH, parse_options_header
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

MIB = 1024 * 1024
MAX_REQUEST_BODY = MIB
# A Content-Length with more digits is over every limit (int() is not tried).
_MAX_LENGTH_DIGITS = 15

# The multipart form of an upload: the file in UPLOAD_FIELD, and at most a
# few small other fields (ignored, and not kept). A file in another field
# counts as the one file of the form, and is not stored.
UPLOAD_FIELD = "file"
UPLOAD_MAX_FIELDS = 16
UPLOAD_MAX_FIELD_BYTES = 64 * 1024
# The names and values of the header lines of one part. A browser sends a
# Content-Disposition with the file name (at most 255 bytes on the disks of a
# pool) and a Content-Type: a few hundred bytes.
UPLOAD_MAX_HEADER_BYTES = 8 * 1024
# At most this much of the file is held in memory: it is written to the
# destination before the buffer would grow over it, and the socket is not
# read while a write is pending.
UPLOAD_BUFFER_BYTES = MIB
# Body chunks are parsed in pieces of at most this size. The file data of one
# piece is at most its size and what the parser held back at the end of the
# previous piece, because it could be the delimiter of the next part:
# "\r\n--", the boundary (at most MAX_BOUNDARY_LENGTH bytes) and the byte
# after it. The buffer is written once it holds _WRITE_AT: it never holds
# more than UPLOAD_BUFFER_BYTES.
_PARSE_BYTES = 256 * 1024
_HELD_BACK = len(b"\r\n--") + MAX_BOUNDARY_LENGTH + 1
_WRITE_AT = UPLOAD_BUFFER_BYTES - _PARSE_BYTES - _HELD_BACK

# The seconds receive_upload waits for each chunk of an upload's body, from
# the client (through Caddy, which streams it); the disk writes between two
# chunks do not count. A client that keeps sending, however slowly, is not
# cut off (ADR-0342).
UPLOAD_IDLE_TIMEOUT = 120

NO_FILE_DETAIL = f"No file was sent: send it as multipart/form-data in the field '{UPLOAD_FIELD}'"
INTERRUPTED_DETAIL = "The upload was interrupted: the client closed the connection"


def idle_detail(seconds: float) -> str:
    return f"The upload timed out: the client sent nothing for {seconds:g} seconds"


# For the API documentation: the endpoints read the form themselves.
UPLOAD_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {UPLOAD_FIELD: {"type": "string", "format": "binary"}},
                    "required": [UPLOAD_FIELD],
                }
            }
        },
    }
}


def _describe(limit: int) -> str:
    return f"{limit // MIB} MiB" if limit % MIB == 0 else f"{limit} bytes"


def too_large_detail(limit: int) -> str:
    return f"Request body too large: the limit is {_describe(limit)}"


def _length_within(value: bytes, limit: int) -> bool:
    """Whether a Content-Length value is plain decimal digits and at most ``limit``."""
    return value.isdigit() and len(value) <= _MAX_LENGTH_DIGITS and int(value) <= limit


class RequestBodyLimitMiddleware:
    """Answer 413 for an HTTP request body over its limit (pure ASGI middleware).

    A Content-Length over the limit, or one that is not plain decimal digits,
    is answered before anything is read. A body without Content-Length
    (chunked) is counted as it is received: once it passes the limit,
    receive() raises HTTPException(413), which FastAPI passes on while it
    reads a body (it turns other errors into 400), and so does
    receive_upload(), so the client gets 413. WebSocket and lifespan scopes
    pass through untouched.

    ``path_limits`` gives paths their own limit; None means no limit.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        limit: int = MAX_REQUEST_BODY,
        path_limits: Mapping[str, int | None] | None = None,
    ) -> None:
        self.app = app
        self.limit = limit
        self.path_limits = dict(path_limits or {})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.path_limits.get(scope["path"], self.limit)
        if limit is None:
            await self.app(scope, receive, send)
            return
        for name, value in scope["headers"]:
            if name == b"content-length" and not _length_within(value, limit):
                response = JSONResponse({"detail": too_large_detail(limit)}, status_code=413)
                await response(scope, receive, send)
                return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            # Once the response has started, the body is only drained by
            # Starlette's wait for a disconnect (FileResponse,
            # StreamingResponse), which keeps nothing: raising there would
            # break a response that is already being sent.
            if message["type"] == "http.request" and not response_started:
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(413, too_large_detail(limit))
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        await self.app(scope, limited_receive, tracking_send)


# --- uploads -------------------------------------------------------------------


class UploadSink:
    """Where receive_upload() puts the file of an upload; one per request.

    The methods block (they use the disk): receive_upload runs each of them
    in a worker thread, one at a time, in this order:

    * prepare(): before any of the body is read;
    * open(filename): as soon as the headers of the file part have arrived,
      before any of its data is written. It checks the client's file name and
      returns the descriptor of a new destination file, which receive_upload
      writes the data to (and closes);
    * finish(): once the whole body has arrived and the file part was
      complete, after receive_upload has closed the descriptor it got from
      open(). A sink that still needs the file open keeps a descriptor of
      its own (the file manager's upload links an unnamed file through one).
      Its result is receive_upload's;
    * discard(): always, and last: also after an error or when the request is
      cancelled (then once the method that was running has returned).
      Nothing runs after it.

    The exceptions of the methods pass through receive_upload unchanged.
    """

    #: The largest file accepted, in bytes; None: no limit.
    max_bytes: int | None = None

    def too_large(self) -> Exception:
        """What receive_upload raises as soon as the file grows over max_bytes."""
        return HTTPException(413, f"The file is too large: the limit is {_describe(self.max_bytes or 0)}")

    def prepare(self) -> None:
        """Check what can be checked before the body is read."""

    def open(self, filename: str) -> int:
        """Check the client's file name; create the destination file and return its descriptor."""
        raise NotImplementedError

    def finish(self) -> Any:
        """Make the complete file final (rename, validate, publish); return the endpoint's result."""
        raise NotImplementedError

    def discard(self) -> None:
        """Release what the upload holds; remove the destination file unless finish() made it final."""


class _Destination:
    """The sink of one upload and the descriptor of its destination file.

    Every call holds a lock and is refused once discard() has run. After a
    cancellation, the call that was running in its worker thread still runs
    to its end; discard() waits for it (it must not close the descriptor
    under a write), and a call that starts after it does nothing.
    """

    def __init__(self, sink: UploadSink) -> None:
        self.sink = sink
        self._fd: int | None = None
        self._lock = threading.Lock()
        self._discarded = False

    def _locked(self, func: Callable[..., Any], *args: Any) -> Any:
        with self._lock:
            if self._discarded:
                raise RuntimeError("The upload was discarded")
            return func(*args)

    async def _call(self, func: Callable[..., Any], *args: Any) -> Any:
        return await asyncio.to_thread(self._locked, func, *args)

    async def prepare(self) -> None:
        await self._call(self.sink.prepare)

    def _open(self, filename: str) -> None:
        self._fd = self.sink.open(filename)

    async def open(self, filename: str) -> None:
        await self._call(self._open, filename)

    def _write(self, data: bytearray) -> None:
        view = memoryview(data)
        while view:
            view = view[os.write(self._fd, view):]

    async def write(self, data: bytearray) -> None:
        await self._call(self._write, data)

    def _finish(self) -> Any:
        fd, self._fd = self._fd, None
        os.close(fd)
        return self.sink.finish()

    async def finish(self) -> Any:
        return await self._call(self._finish)

    def _discard(self) -> None:
        with self._lock:
            self._discarded = True
            try:
                if self._fd is not None:
                    fd, self._fd = self._fd, None
                    os.close(fd)
            except OSError:
                pass
            try:
                self.sink.discard()
            except Exception:
                logger.exception("Could not clean up after an upload")

    async def discard(self) -> None:
        # Shielded: when the request is cancelled, the cleanup still runs to
        # its end in its worker thread (it is not taken off the queue).
        await asyncio.shield(asyncio.get_running_loop().run_in_executor(None, self._discard))


def _decode(value: bytes, charset: str) -> str:
    """Decode a field or file name like Starlette's form parser; latin-1 if the charset cannot.

    UnicodeError, not only UnicodeDecodeError: some codecs raise the base
    class (charset=undefined always does, punycode and idna on some names).
    LookupError: codecs that do not decode bytes to text (rot13, base64).
    """
    try:
        return value.decode(charset)
    except (UnicodeError, LookupError):
        return value.decode("latin-1")


# What the part being read is: another field, the file, or a file in another field.
_FIELD, _FILE, _OTHER_FILE = range(3)


class _UploadForm:
    """Reads a multipart/form-data body with python_multipart's MultipartParser.

    It keeps only what an upload needs, with the limits and the answers of
    Starlette's request.form(max_files=1, max_fields=UPLOAD_MAX_FIELDS,
    max_part_size=UPLOAD_MAX_FIELD_BYTES), and a limit on the headers of a
    part (UPLOAD_MAX_HEADER_BYTES), which Starlette has not. The data of the
    file in UPLOAD_FIELD collects in ``buffer``; other fields are counted and
    dropped, and so is a file in another field.
    """

    def __init__(self, headers: Headers, sink: UploadSink) -> None:
        content_type, params = parse_options_header(headers.get("content-type"))
        if content_type != b"multipart/form-data":
            raise HTTPException(422, NO_FILE_DETAIL)
        charset = params.get(b"charset", b"utf-8").decode("latin-1")
        try:
            self.charset = codecs.lookup(charset).name
        except (LookupError, ValueError):  # ValueError: a NUL in the name
            self.charset = "latin-1"
        try:
            boundary = params[b"boundary"]
        except KeyError:
            raise HTTPException(400, "Missing boundary in multipart.") from None

        self.max_bytes = sink.max_bytes
        self.too_large = sink.too_large
        #: The client's name of the file, once the headers of its part have
        #: arrived and until the destination was opened (take_filename).
        self.filename: str | None = None
        #: File data not written yet.
        self.buffer = bytearray()
        self._files = 0
        self._fields = 0
        self._part = _FIELD
        self._part_bytes = 0
        self._file_bytes = 0
        self._file_seen = False
        self._file_complete = False
        # A field named UPLOAD_FIELD after the file: Starlette's form keeps
        # the last value of a name, so it took the place of the file.
        self._file_replaced = False
        self._header_bytes = 0
        self._header_name = bytearray()
        self._header_value = bytearray()
        self._disposition: bytes | None = None
        callbacks = {
            "on_part_begin": self._on_part_begin,
            "on_header_field": self._on_header_field,
            "on_header_value": self._on_header_value,
            "on_header_end": self._on_header_end,
            "on_headers_finished": self._on_headers_finished,
            "on_part_data": self._on_part_data,
            "on_part_end": self._on_part_end,
        }
        try:
            self._parser = python_multipart.MultipartParser(boundary, callbacks)
        except FormParserError:
            raise HTTPException(400, "Invalid multipart data.") from None

    # The parser's callbacks: they run inside write(), and raise to stop it.

    def _on_part_begin(self) -> None:
        self._part = _FIELD
        self._part_bytes = 0
        self._header_bytes = 0
        self._header_name.clear()
        self._header_value.clear()
        self._disposition = None

    def _count_header(self, size: int) -> None:
        self._header_bytes += size
        if self._header_bytes > UPLOAD_MAX_HEADER_BYTES:
            raise HTTPException(
                400, f"Part headers exceeded maximum size of {UPLOAD_MAX_HEADER_BYTES // 1024}KB."
            )

    def _on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._count_header(end - start)
        self._header_name += memoryview(data)[start:end]

    def _on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._count_header(end - start)
        self._header_value += memoryview(data)[start:end]

    def _on_header_end(self) -> None:
        if self._header_name.lower() == b"content-disposition":
            self._disposition = bytes(self._header_value)
        self._header_name.clear()
        self._header_value.clear()

    def _on_headers_finished(self) -> None:
        _, options = parse_options_header(self._disposition)
        try:
            name = _decode(options[b"name"], self.charset)
        except KeyError:
            raise HTTPException(400, 'The Content-Disposition header field "name" must be provided.') from None
        if b"filename" in options:
            self._files += 1
            if self._files > 1:
                raise HTTPException(400, "Too many files. Maximum number of files is 1.")
            if name == UPLOAD_FIELD:
                self._part = _FILE
                self._file_seen = True
                self.filename = _decode(options[b"filename"], self.charset)
            else:
                self._part = _OTHER_FILE
        else:
            self._fields += 1
            if self._fields > UPLOAD_MAX_FIELDS:
                raise HTTPException(400, f"Too many fields. Maximum number of fields is {UPLOAD_MAX_FIELDS}.")
            if name == UPLOAD_FIELD and self._file_seen:
                self._file_replaced = True

    def _on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._part == _FILE:
            self._file_bytes += end - start
            if self.max_bytes is not None and self._file_bytes > self.max_bytes:
                raise self.too_large()
            self.buffer += memoryview(data)[start:end]
        elif self._part == _FIELD:
            self._part_bytes += end - start
            if self._part_bytes > UPLOAD_MAX_FIELD_BYTES:
                raise HTTPException(
                    400, f"Part exceeded maximum size of {UPLOAD_MAX_FIELD_BYTES // 1024}KB."
                )

    def _on_part_end(self) -> None:
        if self._part == _FILE:
            self._file_complete = True

    # The reader's side.

    def write(self, data: bytes) -> None:
        try:
            self._parser.write(data)
        except FormParserError:
            raise HTTPException(400, "Invalid multipart data.") from None

    def take_filename(self) -> str:
        filename, self.filename = self.filename, None
        return filename

    def take_data(self) -> bytearray:
        data, self.buffer = self.buffer, bytearray()
        return data

    def finish(self) -> None:
        """The body has ended: 422 unless the file part ended (its closing boundary arrived)."""
        self._parser.finalize()
        if not self._file_complete or self._file_replaced:
            raise HTTPException(422, NO_FILE_DETAIL)


async def _next_chunk(stream: AsyncIterator[bytes]) -> bytes | None:
    """The next chunk of the body, or None after its end; 408 when none arrives within UPLOAD_IDLE_TIMEOUT.

    The answer closes the connection (RFC 9110, 408): the rest of the body is
    never read.
    """
    try:
        async with asyncio.timeout(UPLOAD_IDLE_TIMEOUT):
            return await anext(stream)
    except StopAsyncIteration:
        return None
    except TimeoutError:
        raise HTTPException(408, idle_detail(UPLOAD_IDLE_TIMEOUT), headers={"Connection": "close"}) from None


async def receive_upload(request: Request, sink: UploadSink) -> Any:
    """Stream the file of a multipart upload (form field UPLOAD_FIELD) into ``sink``.

    Call it in the endpoint, after the dependencies have checked the
    credentials. Nothing of the body is read before sink.prepare() has
    accepted the request, and none of the file is written before sink.open()
    has accepted its name. The data then goes to the destination file as it
    arrives, written in a worker thread, at most UPLOAD_BUFFER_BYTES at a
    time; the request is not read while a write is pending, so a client
    cannot send faster than the disk takes the file. No temporary file of
    the tempfile module is used, and the whole file is never in memory.

    Returns the result of sink.finish(), which runs once the whole body has
    arrived. Answers like Starlette's request.form() did: 422 when no file
    arrives in that field (also for a body that is not multipart/form-data,
    or one that ends before the closing boundary of the file part); 400 for
    a malformed form, a second file, too many or too large other fields, or
    part headers over UPLOAD_MAX_HEADER_BYTES; 400 when the client goes away
    (not a server error); 408, closing the connection, when the client sends
    nothing for UPLOAD_IDLE_TIMEOUT seconds before the body has ended;
    sink.too_large() as soon as the file grows over sink.max_bytes. The 413
    of RequestBodyLimitMiddleware and the exceptions of the sink pass
    through. Whatever happens, sink.discard() runs at the end.
    """
    destination = _Destination(sink)
    try:
        await destination.prepare()
        form = _UploadForm(request.headers, sink)
        try:
            async with aclosing(request.stream()) as stream:
                while (chunk := await _next_chunk(stream)) is not None:
                    for start in range(0, len(chunk), _PARSE_BYTES):
                        form.write(chunk[start : start + _PARSE_BYTES])
                        if form.filename is not None:
                            await destination.open(form.take_filename())
                        if len(form.buffer) >= _WRITE_AT:
                            await destination.write(form.take_data())
        except ClientDisconnect:
            # A cancelled upload: answered (to nobody) as before, not logged as a server error.
            raise HTTPException(400, INTERRUPTED_DETAIL) from None
        form.finish()
        if form.buffer:
            await destination.write(form.take_data())
        return await destination.finish()
    finally:
        await destination.discard()
