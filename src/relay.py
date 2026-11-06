"""
Módulo de cliente de relay Nostr con WebSockets asíncronos (asyncio).
Incluye suscripción a filtros, publicación de eventos, reconexión automática
con backoff exponencial, cierre limpio sin tareas colgadas y un FakeRelay
en memoria para tests de integración offline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger("relay")


class RelayClient:
    """
    Cliente WebSocket asíncrono para interactuar con un Relay Nostr (NIP-01).
    Soporta suscripciones persistentes que se reenvían al reconectar,
    reconexión con backoff exponencial y cierre determinístico.
    """

    def __init__(
        self,
        url: str,
        on_event: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        initial_backoff: float = 0.5,
        max_backoff: float = 15.0,
        backoff_factor: float = 2.0,
    ):
        self.url = url
        self.on_event = on_event
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.backoff_factor = backoff_factor

        self._running = False
        self._connected = asyncio.Event()
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._loop_task: Optional[asyncio.Task] = None
        self._subscriptions: Dict[str, Dict[str, Any]] = {}
        self._send_queue: asyncio.Queue[str] = asyncio.Queue()

    @property
    def is_connected(self) -> bool:
        """Indica si el websocket está activo y listo para transmitir."""
        return self._connected.is_set() and self._ws is not None

    async def start(self) -> None:
        """Inicia la tarea en segundo plano de conexión y reconexión continua."""
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._reconnect_loop(), name=f"relay-{self.url}")

    async def wait_connected(self, timeout: float = 5.0) -> bool:
        """Espera hasta que la conexión esté establecida o se agote el tiempo."""
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def _reconnect_loop(self) -> None:
        """Bucle de reconexión con retroceso exponencial."""
        backoff = self.initial_backoff

        while self._running:
            try:
                logger.info("Conectando al relay Nostr: %s", self.url)
                async with websockets.connect(
                    self.url,
                    ping_interval=20,
                    ping_timeout=15,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._connected.set()
                    backoff = self.initial_backoff  # Resetear backoff tras conexión exitosa
                    logger.info("Conexión establecida con %s", self.url)

                    # Restaurar suscripciones activas
                    for sub_id, req_filter in self._subscriptions.items():
                        req_msg = json.dumps(["REQ", sub_id, req_filter])
                        await ws.send(req_msg)

                    # Lanzar tareas de lectura y escritura concurrentes
                    reader_task = asyncio.create_task(self._reader(ws))
                    writer_task = asyncio.create_task(self._writer(ws))

                    done, pending = await asyncio.wait(
                        [reader_task, writer_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    for task in pending:
                        task.cancel()
                        try:
                            await task
                        except (asyncio.CancelledError, Exception):
                            pass

            except (ConnectionClosed, OSError, Exception) as e:
                if not self._running:
                    break
                logger.warning("Conexión perdida con %s (%s). Reintentando en %.1fs...", self.url, e, backoff)
            finally:
                self._connected.clear()
                self._ws = None

            if self._running:
                # Añadir jitter para evitar tormentas de reconexión
                jitter = random.uniform(0, 0.2 * backoff)
                sleep_time = min(backoff + jitter, self.max_backoff)
                await asyncio.sleep(sleep_time)
                backoff = min(backoff * self.backoff_factor, self.max_backoff)

    async def _reader(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Escucha mensajes entrantes del relay."""
        async for raw_msg in ws:
            try:
                parsed = json.loads(raw_msg)
                if not isinstance(parsed, list) or not parsed:
                    continue

                msg_type = parsed[0]
                if msg_type == "EVENT" and len(parsed) >= 3:
                    event_dict = parsed[2]
                    if self.on_event and isinstance(event_dict, dict):
                        asyncio.create_task(self.on_event(event_dict))
                elif msg_type == "OK":
                    logger.debug("Respuesta OK del relay: %s", parsed)
                elif msg_type == "NOTICE":
                    logger.info("Aviso del relay: %s", parsed[1] if len(parsed) > 1 else "")
            except Exception as e:
                logger.error("Error procesando mensaje de relay: %s", e)

    async def _writer(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Envía mensajes encolados hacia el relay."""
        while self._running:
            msg = await self._send_queue.get()
            try:
                await ws.send(msg)
            finally:
                self._send_queue.task_done()

    async def subscribe(self, sub_id: str, req_filter: Dict[str, Any]) -> None:
        """Registra una suscripción y la envía al relay."""
        self._subscriptions[sub_id] = req_filter
        if self.is_connected:
            msg = json.dumps(["REQ", sub_id, req_filter])
            await self._send_queue.put(msg)

    async def unsubscribe(self, sub_id: str) -> None:
        """Cierra una suscripción."""
        self._subscriptions.pop(sub_id, None)
        if self.is_connected:
            msg = json.dumps(["CLOSE", sub_id])
            await self._send_queue.put(msg)

    async def publish(self, event: Dict[str, Any]) -> None:
        """Encola un evento para publicación en el relay."""
        msg = json.dumps(["EVENT", event], separators=(",", ":"), ensure_ascii=False)
        await self._send_queue.put(msg)

    async def close(self) -> None:
        """Cierre ordenado y determinístico de la conexión y las tareas de fondo."""
        self._running = False
        self._connected.clear()

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):
                pass
            self._loop_task = None


class FakeRelay:
    """
    Relay Nostr simulado en memoria para pruebas de integración sin conexión a Internet.
    Ejecuta un servidor WebSocket local y enruta eventos matching según NIP-01.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.port = port
        self.server: Optional[websockets.server.WebSocketServer] = None
        self.actual_port: int = 0
        # subscriptions: ws -> dict(sub_id -> filter)
        self.subscriptions: Dict[Any, Dict[str, Dict[str, Any]]] = {}
        self.stored_events: List[Dict[str, Any]] = []

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.actual_port}"

    async def start(self) -> str:
        """Inicia el servidor WebSocket en un puerto disponible y retorna la URL ws://."""
        self.server = await websockets.serve(self._handler, self.host, self.port)
        self.actual_port = self.server.sockets[0].getsockname()[1]
        return self.url

    async def stop(self) -> None:
        """Detiene el servidor y cierra todas las conexiones abiertas."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    def _matches_filter(self, event: Dict[str, Any], req_filter: Dict[str, Any]) -> bool:
        """Comprueba si un evento coincide con un filtro Nostr básico."""
        if "kinds" in req_filter:
            if event.get("kind") not in req_filter["kinds"]:
                return False

        if "authors" in req_filter:
            if event.get("pubkey") not in req_filter["authors"]:
                return False

        if "#p" in req_filter:
            p_tags = [t[1] for t in event.get("tags", []) if len(t) >= 2 and t[0] == "p"]
            target_ps = req_filter["#p"]
            if not any(p in target_ps for p in p_tags):
                return False

        return True

    async def _handler(self, ws: websockets.server.WebSocketServerProtocol) -> None:
        """Maneja el protocolo de cada cliente conectado."""
        self.subscriptions[ws] = {}
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                    if not isinstance(data, list) or not data:
                        continue

                    msg_type = data[0]

                    if msg_type == "EVENT" and len(data) >= 2:
                        event = data[1]
                        self.stored_events.append(event)
                        # Responder OK al emisor
                        await ws.send(json.dumps(["OK", event.get("id", ""), True, ""]))

                        # Difundir a todos los suscriptores matching
                        for client_ws, subs in list(self.subscriptions.items()):
                            for sub_id, req_filter in subs.items():
                                if self._matches_filter(event, req_filter):
                                    try:
                                        await client_ws.send(json.dumps(["EVENT", sub_id, event]))
                                    except Exception:
                                        pass

                    elif msg_type == "REQ" and len(data) >= 3:
                        sub_id = data[1]
                        req_filter = data[2]
                        self.subscriptions[ws][sub_id] = req_filter

                        # Enviar eventos históricos matching si hubiera
                        for ev in self.stored_events:
                            if self._matches_filter(ev, req_filter):
                                await ws.send(json.dumps(["EVENT", sub_id, ev]))

                        await ws.send(json.dumps(["EOSE", sub_id]))

                    elif msg_type == "CLOSE" and len(data) >= 2:
                        sub_id = data[1]
                        self.subscriptions[ws].pop(sub_id, None)

                except Exception as e:
                    logger.error("Error en FakeRelay handler: %s", e)
        finally:
            self.subscriptions.pop(ws, None)
