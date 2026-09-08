import asyncio
import struct
import sys
import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from local_bridge import MAX_PACKET, Session, frame, keycode, parse, pb, receive, touch, unzigzag, read_password_file


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmented_frames_at_header_boundaries(self):
        # Expected headers are independent wire fixtures, not a decoder roundtrip.
        for size, expected in [(0,b'\x00'),(63,b'\xfc'),(64,b'\x01\x01'),
                               (16383,b'\xfd\xff'),(16384,b'\x02\x00\x01')]:
            payload = b'x' * size
            encoded = frame(payload)
            self.assertTrue(encoded.startswith(expected))
            reader = asyncio.StreamReader()
            task = asyncio.create_task(receive(reader))
            for byte in expected:
                reader.feed_data(bytes([byte]))
                await asyncio.sleep(0)
            reader.feed_data(payload)
            self.assertEqual(await task, payload)

    async def test_oversize_header_rejected_without_body(self):
        reader = asyncio.StreamReader()
        reader.feed_data((((MAX_PACKET + 1) << 2) | 3).to_bytes(4,'little'))
        with self.assertRaises(ValueError):
            await receive(reader)

    def test_proto_fixture_and_truncation(self):
        self.assertEqual(pb(9, pb(1,'salt')), bytes.fromhex('4a060a0473616c74'))
        self.assertEqual(parse(bytes.fromhex('080112026f6b')), {1:1, 2:b'ok'})
        for malformed in [b'\x08\x80', b'\x0a\x05ab', b'\x00', b'\x0d\x01',
                          b'\x08' + b'\xff' * 10]:
            with self.assertRaises(ValueError):
                parse(malformed)

    def test_signed_coordinates(self):
        self.assertEqual([unzigzag(v) for v in [0,1,2,3,200]], [0,-1,1,-2,100])

    def test_scrcpy_touch_and_key_wire_layout(self):
        payload = touch(0,100,200,576,1280)
        self.assertEqual(len(payload),32)
        self.assertEqual(payload[:10], b'\x02\x00' + b'\xff'*7 + b'\xfe')
        self.assertEqual(struct.unpack('>iiHHHII',payload[10:]),(100,200,576,1280,65535,0,0))
        self.assertEqual(touch(1,100,200,576,1280)[22:24],b'\0\0')
        self.assertEqual(keycode(4,0),bytes.fromhex('0000000000040000000000000000'))

    async def test_wrong_password_does_not_start_phone(self):
        reader = asyncio.StreamReader()
        for _ in range(5):
            reader.feed_data(frame(pb(7, pb(2,b'wrong'))))
        session = Session(reader,None,SimpleNamespace(), 'test-only-secret')
        session.send = AsyncMock()
        session.phone.start = AsyncMock()
        with self.assertRaisesRegex(ValueError,'authentication failed'):
            await session.run()
        session.phone.start.assert_not_awaited()

    async def test_heartbeat_response_does_not_echo_forever(self):
        reader = asyncio.StreamReader()
        returned_probe = pb(5,pb(1,1234))
        client_probe = pb(5,pb(1,5678)+pb(2,1))
        reader.feed_data(frame(returned_probe)+frame(client_probe)+frame(pb(19,pb(9,'close'))))
        session = Session(reader,None,SimpleNamespace(),'test-only-secret')
        session.send = AsyncMock()
        await session.inputs()
        session.send.assert_awaited_once_with(client_probe)

    def test_backend_password_file_permissions_and_length(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/'backend-password'
            p.write_text('test-only-password-with-32-characters')
            p.chmod(0o600)
            self.assertEqual(read_password_file(p), p.read_text())
            p.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'owner'):
                read_password_file(p)
            p.chmod(0o600)
            p.write_text('short')
            with self.assertRaisesRegex(ValueError, '24 to 256'):
                read_password_file(p)

    async def test_disconnect_cancels_pressed_finger(self):
        session = Session(None,None,SimpleNamespace(),'test-only-secret')
        session.phone.width,session.phone.height = 576,1280
        session.down = True
        session.x,session.y = 100,200
        session.control = AsyncMock()
        await session.release()
        session.control.assert_awaited_once_with(touch(3,100,200,576,1280))
        self.assertFalse(session.down)

    async def test_click_and_drag_keep_position_on_coordinate_less_buttons(self):
        # RustDesk wire fixture: move (100,200), left down with omitted zero
        # fields, drag to (300,400), left up with explicit zero placeholders.
        # This exercises the full receive -> protobuf -> scrcpy control path.
        reader = asyncio.StreamReader()
        for payload in ['520610c801189003', '52020809',
                        '5208080810d80418a006', '5206080a10001800']:
            reader.feed_data(frame(bytes.fromhex(payload)))
        reader.feed_data(frame(pb(19,pb(9,'close'))))
        session = Session(reader,None,SimpleNamespace(),'test-only-secret')
        session.phone.width,session.phone.height = 576,1280
        session.control = AsyncMock()
        await session.inputs()
        packets = [c.args[0] for c in session.control.await_args_list]
        self.assertEqual([p[1] for p in packets], [0,2,1])
        self.assertEqual([struct.unpack('>iiHH',p[10:22]) for p in packets],
                         [(100,200,576,1280),(300,400,576,1280),(300,400,576,1280)])
        self.assertFalse(session.down)

    async def test_authenticated_android_target_defers_even_without_client_platform(self):
        import hashlib
        reader = asyncio.StreamReader()
        password = 'test-only-secret'
        digest = hashlib.sha256(hashlib.sha256((password+'salt').encode()).digest()+b'challenge').digest()
        reader.feed_data(frame(pb(7,pb(2,digest)+pb(6,pb(10,pb(2,1))))))
        session = Session(reader,None,SimpleNamespace(),password)
        session.send = AsyncMock()
        session.phone.start = AsyncMock(side_effect=RuntimeError('stop after auth'))
        with patch('local_bridge.secrets.token_hex',side_effect=['salt','challenge']):
            with self.assertRaisesRegex(RuntimeError,'stop after auth'):
                await session.run()
        self.assertTrue(session.mobile_touch)

    async def test_mobile_stationary_tap_does_not_inject_recognizer_dwell(self):
        reader = asyncio.StreamReader()
        session = Session(reader,None,SimpleNamespace(),'test-only-secret')
        session.mobile_touch = True
        session.phone.width,session.phone.height = 576,1280
        session.control = AsyncMock()
        inputs = asyncio.create_task(session.inputs())
        deadline = asyncio.create_task(session.touch_deadlines())
        try:
            reader.feed_data(frame(pb(10,pb(2,200)+pb(3,400)))+frame(pb(10,pb(1,9))))
            await asyncio.sleep(.35)
            session.control.assert_not_awaited()
            reader.feed_data(frame(pb(10,pb(1,9)))+frame(pb(10,pb(1,10)))+frame(pb(19,pb(9,'close'))))
            await inputs
            self.assertEqual([c.args[0][1] for c in session.control.await_args_list], [0,1])
        finally:
            inputs.cancel(); deadline.cancel()
            await asyncio.gather(inputs,deadline,return_exceptions=True)

    async def test_mobile_deliberate_hold_commits_before_release(self):
        session = Session(None,None,SimpleNamespace(),'test-only-secret')
        session.mobile_touch = True
        session.phone.width,session.phone.height = 576,1280
        session.down = True
        session.pending_since = __import__('time').monotonic() - .51
        session.control = AsyncMock()
        deadline = asyncio.create_task(session.touch_deadlines())
        try:
            await asyncio.sleep(.01)
            self.assertEqual([c.args[0][1] for c in session.control.await_args_list], [0])
            await session.release()
            self.assertEqual([c.args[0][1] for c in session.control.await_args_list], [0,3])
        finally:
            deadline.cancel()
            await asyncio.gather(deadline,return_exceptions=True)

    async def test_mobile_drag_commits_and_pending_disconnect_does_not_tap(self):
        reader = asyncio.StreamReader()
        for mask,x,y in [(0,100,200),(9,0,0),(0,300,400),(10,0,0)]:
            reader.feed_data(frame(pb(10,pb(1,mask)+pb(2,x*2)+pb(3,y*2))))
        reader.feed_data(frame(pb(19,pb(9,'close'))))
        session = Session(reader,None,SimpleNamespace(),'test-only-secret')
        session.mobile_touch = True
        session.phone.width,session.phone.height = 576,1280
        session.control = AsyncMock()
        await session.inputs()
        self.assertEqual([c.args[0][1] for c in session.control.await_args_list], [0,2,1])
        session.control.reset_mock()
        session.down = True
        session.touch_injected = False
        await session.release()
        session.control.assert_not_awaited()

    async def test_mobile_back_home_and_recents_wire_events(self):
        # Back=button 8; Home=short middle; Apps=500 ms middle. No action on DOWN.
        for events, times, expected in [
            ([65,66], [], 4), ([17,18], [], 4),
            ([33,34], [10.0,10.01], 3), ([33,34], [10.0,10.5], 187),
            ([33,33,34], [10.0,10.5], 187),
            ([33], [10.0], None), ([34], [], None),
        ]:
            reader = asyncio.StreamReader()
            for mask in events:
                reader.feed_data(frame(pb(10,pb(1,mask))))
            reader.feed_data(frame(pb(19,pb(9,'close'))))
            session = Session(reader,None,SimpleNamespace(),'test-only-secret')
            session.control = AsyncMock()
            # Replace this module's time binding, not the event loop clock.
            with patch('local_bridge.time') as clock:
                clock.monotonic.side_effect = times
                await session.inputs()
            if expected is None:
                session.control.assert_not_awaited()
            else:
                session.control.assert_awaited_once_with(keycode(expected,0)+keycode(expected,1))

    async def test_mobile_duplicate_down_and_orphan_up_are_not_injected(self):
        reader = asyncio.StreamReader()
        # Mobile recognizer sends MOVE, DOWN, MOVE, DOWN, UP for one tap.
        # Include a stray release before/after to check the contact boundary.
        for payload in ['5202080a', '520610c801189003', '52020809',
                        '520610c801189003', '52020809', '5202080a', '5202080a']:
            reader.feed_data(frame(bytes.fromhex(payload)))
        reader.feed_data(frame(pb(19,pb(9,'close'))))
        session = Session(reader,None,SimpleNamespace(),'test-only-secret')
        session.phone.width,session.phone.height = 576,1280
        session.control = AsyncMock()
        await session.inputs()
        self.assertEqual([c.args[0][1] for c in session.control.await_args_list], [0,2,1])
        self.assertEqual(session.duplicate_downs, 1)
        self.assertFalse(session.down)

    async def test_move_to_origin_is_not_treated_as_missing_position(self):
        reader = asyncio.StreamReader()
        # Move to bottom/right, then legitimately move to (0,0), then click.
        for payload in ['520610c801189003', '5200', '52020809', '5202080a']:
            reader.feed_data(frame(bytes.fromhex(payload)))
        reader.feed_data(frame(pb(19,pb(9,'close'))))
        session = Session(reader,None,SimpleNamespace(),'test-only-secret')
        session.phone.width,session.phone.height = 576,1280
        session.control = AsyncMock()
        await session.inputs()
        self.assertEqual([struct.unpack('>ii',c.args[0][10:18])
                          for c in session.control.await_args_list], [(0,0),(0,0)])


if __name__ == '__main__':
    unittest.main()
