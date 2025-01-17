import chardet, cv2, time, re, os, numpy as np, threading

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
import pygame, pygame.scrap
from pyvidplayer2 import Video
from dataclasses import dataclass
from typing import Optional
from gui import screen, cache
from gui.addon import ascii, subtitle, with_play, fft
import gui.font
from download import download, subtitles
from sockets import client, server
from sockets import setting as socket_setting
from setting import setting as user_setting
import discord_rpc.client
if user_setting.stt:
    from gui.addon.control import stt
if user_setting.Gesture or user_setting.Gesture_show:
    from gui.addon.control import gesture

# Global state
@dataclass
class VideoState:
    cap: Optional[cv2.VideoCapture] = None
    ascii_mode: bool = False
    ascii_width: int = 190 
    font_size: int = 14
    font: Optional[pygame.font.Font] = None
    search: str = ""
    search_width: int = 0
    search_height: int = 0
    fullscreen: bool = False
    display_width: int = 0
    display_height: int = 0
    audio: Optional[np.ndarray] = None
    msg_start_time: float = 0
    msg_text: str = ""

state = VideoState()

def is_url(url: str) -> bool:
    match = re.search(cache.SEARCH_PATTERN, url)
    return bool(match)
def is_playlist(url: str) -> bool:
    match = re.search(cache.PLAYLIST_SEARCH_PATTERN, url)
    return bool(match)

def handle_key_event(key: str) -> None:
    """
    Handles key events for controlling video playback and settings.
    Parameters:
    key (str): The key pressed by the user. Supported keys are:
        - 's': Skip to the end of the video.
        - 'escape': Stop the video and clear the video list.
        - 'r': Restart the video.
        - 'p': Pause or resume the video.
        - 'm': Mute or unmute the video.
        - 'l': Toggle loop mode.
        - 'up': Increase the volume.
        - 'down': Decrease the volume.
        - 'right': Seek forward.
        - 'left': Seek backward.
        - 'f11': Toggle fullscreen mode.
        - 'a': Toggle ASCII mode.
    Returns:
    None
    """
    if not key:
        return
    match key:
        case "s":
            screen.vid.seek(screen.vid.duration - screen.vid.get_pos())
        case "escape":
            cache.video_list = []
            screen.vid.seek(screen.vid.duration - screen.vid.get_pos())
        case "r":
            screen.vid.restart()
            state.msg_text = "Restarted"
        case "p":
            screen.vid.toggle_pause()
            state.msg_text = "Paused" if screen.vid.paused else "Playing"
        case "m":
            screen.vid.toggle_mute()
            state.msg_text = "Muted" if screen.vid.muted else "Unmuted"
        case "l":
            cache.loop = not cache.loop
            state.msg_text = f"Loop: {'On' if cache.loop else 'Off'}"
        case "f":
            user_setting.FFT = not user_setting.FFT
            user_setting.change_setting_data('FFT', user_setting.FFT)
            state.msg_text = f"FFT: {user_setting.FFT}"
        case "up" | "down":
            volume_delta = 10 if key == "up" else -10
            if 0 <= user_setting.volume + volume_delta <= 100:
                user_setting.change_setting_data('volume',user_setting.volume + volume_delta)
                screen.vid.set_volume(user_setting.volume/100)
                state.msg_text = f"Volume: {user_setting.volume}%"
        case "right" | "left":
            seek_amount = 15 if key == "right" else -15
            screen.vid.seek(seek_amount)
            state.msg_text = f"Seek: {seek_amount:+d}s"
        case "f11":
            state.fullscreen = not state.fullscreen
            state.msg_text = f"{'FullScreen' if state.fullscreen else 'BasicScreen'}"
            if not state.fullscreen:
                screen.reset((screen.vid.current_size[0]*1.5, screen.vid.current_size[1]*1.5+5))
            else:
                screen.reset((state.display_width,state.display_height))
        case "a":
            ascii.toggle(state)
            state.msg_text = "ASCII Mode" if state.ascii_mode else "Normal Mode"
        case _:
            state.msg_text = ""
        
    if state.msg_text:
        state.msg_start_time = time.time()


def try_play_video(url: str, max_retries: int = 10) -> None:
    """Attempts to play a video from the given URL, retrying up to a specified number of times if an exception occurs."""
    for retry in range(max_retries):
        try:
            run(url)
            return
        except Exception as e:
            if retry == max_retries - 1:
                print("Failed to play video after maximum retries")
                return
            print(f"Retry {retry + 1}/{max_retries}: {str(e)}")
            time.sleep(0.5)


def run(url: str, seek = 0):
    global state
    os.environ['SDL_VIDEO_CENTERED'] = '1'
    fns, fn, vtt = download.install(url)

    sub = None
    if vtt:
        try:
            sub = subtitles.parse_vtt_file(vtt)
        except Exception as e:
            sub = None
            vtt = None

    screen.vid = Video(fn)
    screen.reset((screen.vid.current_size[0]*1.5, screen.vid.current_size[1]*1.5 + 5), vid=True)
    pygame.display.set_caption(screen.vid.name)
    screen.vid.set_volume(user_setting.volume / 100)
    screen.vid.seek(seek)

    state.audio = fft.extract_audio_from_video(fn)
    state.font = pygame.font.SysFont("Courier", state.font_size)
    state.cap = cv2.VideoCapture(fn)
    state.msg_start_time = 0 
    state.msg_text = "" 

    if user_setting.stt:
        threading.Thread(target=stt.run, args=(screen.vid,), daemon=True).start()
    if with_play.server:
        server.seek = 0
        server.playurl = url
        #server.broadcast_message({"type":"play-info","playurl": url,"seek": 0})

    while screen.vid.active:
        key = None
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                screen.vid.stop()
            elif event.type == pygame.KEYDOWN:
                key = pygame.key.name(event.key)
                handle_key_event(key)
        
        if screen.load == 2:
            current_time = screen.vid.get_pos()
            total_length = screen.vid.duration
            fps = state.cap.get(cv2.CAP_PROP_FPS)
            frame_number = int(current_time * fps)
            state.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ret, frame = state.cap.read()

            if total_length - current_time <= 0.1 and cache.loop:
                screen.vid.restart()

            if not ret:
                break

            if user_setting.Gesture:
               gesture.run(screen.vid)

            if state.ascii_mode:
                ascii.render(frame, current_time, total_length, state)
            else:
                screen.render(frame, current_time, total_length, state)
        if sub:
            subtitle.render(sub)
        if user_setting.FFT:
            fft.run(state.audio)

        pygame.display.update()
        pygame.time.wait(16)

    # * Clean up
    if state.cap:
        state.cap.release()
        state.cap = None

    screen.vid.close()
    if user_setting.stt:
        stt.stop()
    if user_setting.Gesture and user_setting.Gesture_show:
        gesture.close()
        
    if cache.video_list:
        try:
            cache.video_list.pop(0)
        except IndexError:
            cache.video_list = []

    if state.fullscreen:
        screen.reset((state.display_width,state.display_height))
    if not cache.video_list:
        screen.reset((state.search_width, state.search_height))
    
    download.clear(fns)
    os.environ['SDL_VIDEO_CENTERED'] = '1'
    pygame.display.update()
    pygame.display.set_caption("Sclat Video Player")
    discord_rpc.client.default()


def wait(once):
    global state
    screen_info = pygame.display.Info()
    state.display_width = screen_info.current_w
    state.display_height = screen_info.current_h
    state.search_width = state.display_width // 2
    state.search_height = int(state.search_width * (9 / 16))
    os.environ['SDL_VIDEO_CENTERED'] = '1'

    # * Setup display
    if state.fullscreen:
        screen.reset((state.display_width,state.display_height))
    elif screen.vid is None:
        screen.reset((state.search_width, state.search_height))
    else:
        screen.reset((screen.vid.current_size[0]*1.5, screen.vid.current_size[1]*1.5 + 5), vid=True)
    
    # * Initialize screen
    pygame.scrap.init()
    icon = pygame.image.load("./asset/sclatIcon.png")
    pygame.display.set_icon(icon)
    pygame.display.set_caption("Sclat Video Player")
    pygame.key.set_text_input_rect(pygame.Rect(0, 0, 0, 0))

    discord_rpc.client.default()
    if with_play.server:
        server.seek = 0
        server.playurl = ''
        #server.broadcast_message({"type":"play-wait"})

    last_playinfo_time = time.time()

    if socket_setting.last_server != "":
        with_play.c_server_ip = socket_setting.last_server

    while True:
        screen.win.fill((0, 0, 0))
        if with_play.client:
            if client.play:
                pygame.display.update()
                run(client.url, client.seek)
            else:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        pygame.display.quit()
                        pygame.quit()
                        return 
                    elif event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            if user_setting.discord_RPC:
                                discord_rpc.client.RPC.close()
                            pygame.quit()
                            exit(0)
                        elif event.key == pygame.K_BACKSPACE:
                            with_play.c_server_ip = with_play.c_server_ip[:-1]
                        elif event.key == pygame.K_RETURN:
                            socket_setting.change_setting_data("last-server", with_play.c_server_ip)
                            with_play.Start_Client(with_play.c_server_ip)
                        elif event.key == pygame.K_v and (pygame.key.get_mods() & pygame.KMOD_CTRL):
                            if pygame.scrap.get_init():
                                copied_text = pygame.scrap.get(pygame.SCRAP_TEXT)
                                if copied_text:
                                    try:
                                        copied_text = copied_text.decode('utf-8').strip('\x00')
                                    except UnicodeDecodeError:
                                        detected = chardet.detect(copied_text)
                                        encoding = detected['encoding']
                                        copied_text = copied_text.decode(encoding).strip('\x00')
                                    gui.with_play.c_server_ip += copied_text
                    elif event.type == pygame.TEXTINPUT:
                        gui.with_play.c_server_ip += event.text
                if gui.with_play.c_server_on:
                    current_time = time.time()
                    if current_time - last_playinfo_time >= 1:
                        client.playinfo()
                        last_playinfo_time = current_time
                    text_surface = screen.font.render("Waiting for the server to play the song", True, (255,255,255))
                    text_rect = text_surface.get_rect(center=(screen.win.get_size()[0]/2,screen.win.get_size()[1]/2)) 
                    screen.win.blit(text_surface, text_rect)
                    pygame.display.update()
                else:
                    text_surface = screen.font.render(f"Server IP: {gui.with_play.c_server_ip}", True, (255,255,255))
                    text_rect = text_surface.get_rect(center=(screen.win.get_size()[0]/2,screen.win.get_size()[1]/2)) 
                    screen.win.blit(text_surface, text_rect)
                    pygame.display.update()
        else:
            key = None
            if len(cache.video_list) == 0:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        pygame.display.quit()
                        pygame.quit()
                        return 
                    elif event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            if user_setting.discord_RPC:
                                discord_rpc.client.RPC.close()
                            pygame.quit()
                            exit(0)
                        elif event.key == pygame.K_BACKSPACE:
                            state.search = state.search[:-1]
                        elif event.key == pygame.K_RETURN:
                            key = "return"
                        elif event.key == pygame.K_v and (pygame.key.get_mods() & pygame.KMOD_CTRL):
                            if pygame.scrap.get_init():
                                copied_text = pygame.scrap.get(pygame.SCRAP_TEXT)
                                if copied_text:
                                    try:
                                        copied_text = copied_text.decode('utf-8').strip('\x00')
                                    except UnicodeDecodeError:
                                        detected = chardet.detect(copied_text)
                                        encoding = detected['encoding']
                                        copied_text = copied_text.decode(encoding).strip('\x00')
                                    state.search += copied_text
                    elif event.type == pygame.TEXTINPUT:
                        state.search += event.text
                if with_play.server:
                    server.seek = 0
                    server.playurl = ''
                if not key:
                    text_surface = screen.font.render(f"search video : {state.search}", True, (255,255,255))
                    text_rect = text_surface.get_rect(center=(screen.win.get_size()[0]/2,screen.win.get_size()[1]/2)) 
                    screen.win.blit(text_surface, text_rect)
                    pygame.display.update()
                    continue
                elif key == "backspace":
                    state.search = state.search[0:len(state.search)-1]
                elif len(key) == 1:
                    state.search = state.search + key
                text_surface = screen.font.render(f"search video : {state.search}", True, (255,255,255))
                text_rect = text_surface.get_rect(center=(screen.win.get_size()[0]/2,screen.win.get_size()[1]/2)) 
                screen.win.blit(text_surface, text_rect)
                pygame.display.update()
                if key == "enter" or key == "return":
                    if is_playlist(state.search):
                        video_urls = download.get_playlist_video(state.search)
                        cache.video_list.extend(video_urls)
                        state.search = ""
                    elif is_url(state.search):
                        a = state.search
                        cache.video_list.append(a)
                        state.search = ""
                    else:
                        screen.win.fill((0,0,0))
                        text_surface = screen.font.render(f"Searching YouTube videos...", True, (255,255,255))
                        text_rect = text_surface.get_rect(center=(screen.win.get_size()[0]/2,screen.win.get_size()[1]/2)) 
                        screen.win.blit(text_surface, text_rect)
                        pygame.display.flip()
                        load = False
                        choice = 0
                        videos = download.search(state.search,10)[:5]
                        screen.win.fill((0,0,0))
                        pygame.display.flip()
                        while True:
                            key = ""
                            for event in pygame.event.get():
                                if event.type == pygame.QUIT:
                                    pygame.display.quit()
                                    pygame.quit()
                                    return  
                                elif event.type == pygame.KEYDOWN:
                                    key = pygame.key.name(event.key)
                            if key == "up":
                                if choice != 0:
                                    choice -= 1
                                else:
                                    choice = len(videos) - 1
                            elif key == "down":
                                if choice != len(videos) - 1:
                                    choice += 1
                                else:
                                    choice = 0
                            elif key == "escape":
                                cache.video_list = []
                                break
                            screen.win.fill((0,0,0))
                            for i, video in enumerate(videos):
                                if i == choice:
                                    text_surface = screen.font.render(video.title, True, (0,0,255))
                                else:
                                    text_surface = screen.font.render(video.title, True, (255,255,255))
                                text_rect = text_surface.get_rect()
                                text_rect.centerx = screen.win.get_size()[0] // 2
                                text_rect.y = i * 30 + 50
                                screen.win.blit(text_surface, text_rect)
                                if not load:
                                    pygame.display.flip()
                            load = True
                            pygame.display.flip()
                            if key == "enter" or key == "return":
                                cache.video_list.append(f"https://www.youtube.com/watch?v={videos[choice].watch_url}")
                                break
                trys = 0
                while len(cache.video_list) != 0:
                    try:
                        run(cache.video_list[0])
                        if once:
                            break
                    except Exception as e:
                        if screen.vid == None:
                            screen.reset((state.search_width, state.search_height))
                        else:
                            screen.reset((screen.vid.current_size[0]*1.5,screen.vid.current_size[1]*1.5+5), vid=True)
                        if trys >= 10:
                            print("fail")
                            cache.video_list = []
                            break
                        print(f"An error occurred during playback. Trying again... ({trys}/10) > \n{e}")
                        text_surface = screen.font.render(f"An error occurred during playback. Trying again... ({trys}/10) >", True, (255,255,255))
                        text_surface_2 = screen.font.render(f"{e}", True, (255,255,255))
                        text_rect = text_surface.get_rect(center=(screen.win.get_size()[0]/2,screen.win.get_size()[1]/2)) 
                        text_rect_2 = text_surface_2.get_rect(center=(screen.win.get_size()[0]/2,screen.win.get_size()[1]/2+30)) 
                        screen.win.blit(text_surface, text_rect)
                        screen.win.blit(text_surface_2, text_rect_2)
                        pygame.display.flip()
                        time.sleep(0.5)
                        trys += 1