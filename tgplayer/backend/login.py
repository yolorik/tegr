"""Вход в аккаунт: номер, код, пароль и QR."""

import asyncio

from ..system import telethon_boot
from .common import _normalize_phone


class LoginMixin:
    async def _ask_login(self, kind, message):
        """Спрашивает данные входа у пользователя и ждёт ответа.

        Выполняется в потоке backend'а: сигнал уходит в Qt-поток, там
        показывается диалог, а ответ возвращается сюда через
        answer_login(). None означает отказ пользователя.
        """
        fut = self.loop.create_future()
        self._login_future = fut
        self.login_step.emit(kind, message)
        try:
            return await fut
        finally:
            if self._login_future is fut:
                self._login_future = None

    def answer_login(self, value):
        """Ответ пользователя на шаг входа (вызывается из Qt-потока)."""
        fut = self._login_future
        if fut is None or fut.done() or self.loop is None:
            return
        try:
            self.loop.call_soon_threadsafe(fut.set_result, value)
        except RuntimeError:
            pass

    async def qr_login_flow(self, on_url, on_state):
        """Вход по QR-коду: телефон сканирует код с экрана.

        Нужен потому, что Telegram не всегда присылает код по SMS: если у
        номера уже есть активная сессия, он отправляет код В ПРИЛОЖЕНИЕ —
        в служебный чат «Telegram». Человек ждёт SMS, ничего не приходит,
        и войти невозможно. QR снимает вопрос вовсе: код не нужен.

        on_url(str) — отдать ссылку tg://login?token=... (нарисовать QR);
        on_state(str) — сообщить состояние: 'scan' (ждём сканирования),
        'confirm' (отсканировали, подтвердите на телефоне), 'expired'
        (код устарел, обновляем).

        Возвращает 'ok', 'cancel' или 'error' — как _interactive_login.
        """
        client = self.client
        if client is None:
            return 'error'

        if await client.is_user_authorized():
            return 'ok'

        # Сбрасываем флаг «человек отказался». Окно входа закрывается
        # кнопкой «Войти по QR» как обычное «принято», но backend до этого
        # мог уже пометить вход отменённым — и тогда цикл ниже не сделал
        # бы ни одного шага, а программа закрылась бы.
        self._stop_connect = False

        try:
            qr = await client.qr_login()
        except Exception as ex:
            print(f"[Вход] QR не получен: {ex}")
            return 'error'

        on_url(qr.url)
        on_state('scan')

        # Ждём подтверждения. Каждый wait — это один цикл ожидания;
        # когда код устаревает, Telethon бросает TimeoutError, и код
        # нужно перерисовать (recreate). Так повторяем, пока человек
        # не отменит вход.
        while not self._stop_connect and not self._is_closed():
            try:
                await qr.wait(timeout=30)
                print("[Вход] QR подтверждён")
                return 'ok'
            except asyncio.TimeoutError:
                # Код устарел — рисуем новый. Это нормальный ход событий:
                # QR живёт около минуты.
                if self._stop_connect or self._is_closed():
                    break
                try:
                    await qr.recreate()
                    on_url(qr.url)
                    on_state('expired')
                    print("[Вход] QR обновлён")
                except Exception as ex:
                    print(f"[Вход] QR не обновлён: {ex}")
                    return 'error'
            except telethon_boot.SessionPasswordNeededError:
                # Включена двухфакторная защита: после сканирования нужен
                # облачный пароль.
                password = await self._ask_login(
                    'password', "Включена двухфакторная защита. Введите пароль")
                if not password:
                    return 'cancel'
                try:
                    await client.sign_in(password=password)
                    return 'ok'
                except Exception as ex:
                    print(f"[Вход] пароль не принят: {ex}")
                    return 'error'
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                print(f"[Вход] QR: {type(ex).__name__}: {ex}")
                return 'error'

        return 'cancel'

    async def _interactive_login(self):
        """Вход в аккаунт: номер, код, при необходимости пароль 2FA.

        Возвращает 'ok', 'cancel' (пользователь отказался или данные
        неверны) либо 'error' (сеть или Telegram не дали войти — есть
        смысл попробовать другой способ подключения).
        """
        client = self.client
        phone = await self._ask_login(
            'phone', "Введите номер телефона в международном формате, "
                     "например +79991234567")
        if not phone:
            return 'cancel'
        phone = _normalize_phone(phone)
        if len(phone) < 8:
            await self._ask_login('error', "Номер выглядит неполным.")
            return 'cancel'

        print(f"[Вход] запрашиваю код для {phone}")
        try:
            sent = await client.send_code_request(phone)
        except telethon_boot.PhoneNumberInvalidError:
            print(f"[Вход] Telegram не принял номер: {phone}")
            await self._ask_login('error', "Telegram не принял этот номер.")
            return 'cancel'
        except Exception as ex:
            print(f"[Вход] код не отправлен: {ex}")
            await self._ask_login('error', f"Не удалось отправить код: {ex}")
            return 'error'

        print(f"[Вход] код отправлен для {phone}")
        code = await self._ask_login(
            'code', "Код отправлен в Telegram. Введите его сюда")
        if not code:
            return 'cancel'
        code = ''.join(ch for ch in code if ch.isdigit()) or code

        try:
            await client.sign_in(phone=phone, code=code,
                                 phone_code_hash=sent.phone_code_hash)
        except telethon_boot.SessionPasswordNeededError:
            password = await self._ask_login(
                'password', "Включена двухфакторная защита. Введите пароль")
            if not password:
                return 'cancel'
            try:
                await client.sign_in(password=password)
            except telethon_boot.PasswordHashInvalidError:
                await self._ask_login('error', "Неверный пароль.")
                return 'cancel'
            except Exception as ex:
                print(f"[Вход] пароль не принят: {ex}")
                await self._ask_login('error', f"Вход не выполнен: {ex}")
                return 'error'
        except (telethon_boot.PhoneCodeInvalidError, telethon_boot.PhoneCodeExpiredError):
            await self._ask_login(
                'error', "Код неверный или устарел. Запустите программу "
                         "заново и запросите новый код.")
            return 'cancel'
        except Exception as ex:
            print(f"[Вход] код не принят: {ex}")
            await self._ask_login('error', f"Вход не выполнен: {ex}")
            return 'error'

        who = ''
        try:
            me = await client.get_me()
            if me is not None:
                who = (f" (@{me.username})" if me.username
                       else f" (id {me.id})")
        except Exception:
            pass
        print(f"[Вход] выполнен{who}")
        self.status_changed.emit("Вход выполнен.")
        return 'ok'
