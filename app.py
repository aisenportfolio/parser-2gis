"""Парсер 2ГИС как приложение: своё окно вместо вкладки браузера."""
import ctypes
import sys
import threading
from http.server import ThreadingHTTPServer

import webview

import server


def main():
    # второй экземпляр не запускаем — иначе два парсера дерутся за один профиль браузера
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Parser2GIS_single_instance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(None, "Парсер 2ГИС уже запущен.", "Парсер 2ГИС", 0x40)
        sys.exit(0)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.H)  # свободный порт
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    webview.settings["ALLOW_DOWNLOADS"] = True  # кнопка «Скачать Excel» (по умолчанию загрузки запрещены)
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True  # WhatsApp, 2ГИС, сайты — в обычном браузере
    win = webview.create_window("Парсер 2ГИС", url, width=1400, height=880, min_size=(900, 600),
                                background_color="#0f1115",
                                text_select=True)  # по умолчанию выделять текст нельзя

    def on_closing():
        if server.STATE["running"]:
            if not win.create_confirmation_dialog(
                    "Идёт сбор", "Сбор ещё идёт. Закрыть? Уже проверенные компании сохранятся."):
                return False
            if server.CURRENT["scraper"]:
                server.CURRENT["scraper"].stop_flag = True
        return True

    win.events.closing += on_closing
    webview.start()
    srv.shutdown()
    del mutex


if __name__ == "__main__":
    main()
