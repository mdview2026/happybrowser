# -*- coding: utf-8 -*-
"""屏幕录制（移植精简自 DrissionPage © g1879 _units/screencast.py，https://DrissionPage.cn
—— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
改造点：文案中文内联；video 模式的 cv2/numpy 依赖改为使用时报错提示安装。"""
from base64 import b64decode
from os.path import sep
from pathlib import Path
from random import randint
from shutil import rmtree
from tempfile import gettempdir
from threading import Thread
from time import sleep, time


class Screencast(object):
    def __init__(self, owner):
        self._owner = owner
        self._path = None
        self._tmp_path = None
        self._running = False
        self._enable = False
        self._mode = 'video'

    @property
    def set_mode(self):
        return ScreencastMode(self)

    def start(self, save_path=None):
        self.set_save_path(save_path)
        if self._path is None:
            raise RuntimeError('必须填写 save_path 参数。')

        if self._mode in ('frugal_video', 'video'):
            self._tmp_path = Path(gettempdir()) / 'happybrowser' / f'screencast_tmp_{time()}_{randint(0, 100)}'
            self._tmp_path.mkdir(parents=True, exist_ok=True)

        if self._mode.startswith('frugal'):
            self._owner._set_callback('Page.screencastFrame', self._onScreencastFrame)
            self._owner._run_cdp('Page.startScreencast', everyNthFrame=1, quality=100)

        elif not self._mode.startswith('js'):
            self._running = True
            self._enable = True
            Thread(target=self._run).start()

        else:  # js 模式：getDisplayMedia 需要用户手势选择录制目标
            js = '''
            async function () {
                stream = await navigator.mediaDevices.getDisplayMedia({video: true, audio: true})
                mime = MediaRecorder.isTypeSupported("video/webm; codecs=vp9")
                               ? "video/webm; codecs=vp9"
                               : "video/webm"
                mediaRecorder = new MediaRecorder(stream, {mimeType: mime})
                HB_Screencast_chunks = [];
                mediaRecorder.addEventListener('dataavailable', function(e) {
                    HB_Screencast_blob_ok = false;
                    HB_Screencast_chunks.push(e.data);
                    HB_Screencast_blob_ok = true;
                })
                mediaRecorder.start()

                mediaRecorder.addEventListener('stop', function(){
                    while(HB_Screencast_blob_ok==false){}
                    HB_Screencast_blob = new Blob(HB_Screencast_chunks,
                                                  {type: HB_Screencast_chunks[0].type});
                })
              }
            '''
            print('请在浏览器中选择录制目标。')
            self._owner._run_js('var HB_Screencast_blob;var HB_Screencast_blob_ok=false;')
            self._owner._run_js(js)
        print('开始录制。')

    def stop(self, video_name=None, suffix='mp4', coding='mp4v'):
        video_name = f'{time()}.{suffix}' if not video_name else video_name
        if not video_name.endswith(f'.{suffix}'):
            video_name = f'{video_name}.{suffix}'
        path = f'{self._path}{sep}{video_name}'

        if self._mode.startswith('js'):
            self._owner._run_js('mediaRecorder.stop();', as_expr=True)
            while not self._owner._run_js('return HB_Screencast_blob_ok;'):
                sleep(.05)
            with open(path, 'wb') as f:
                f.write(b64decode(self._owner._run_js('return HB_Screencast_blob;')))
            self._owner._run_js('HB_Screencast_blob_ok = false;'
                                'HB_Screencast_chunks = [];'
                                'HB_Screencast_blob = null', as_expr=True)
            print('录制结束。')
            return path

        if self._mode.startswith('frugal'):
            self._owner._set_callback('Page.screencastFrame', None)
            self._owner._run_cdp('Page.stopScreencast')
        else:
            self._enable = False
            while self._running:
                sleep(.01)

        if self._mode.endswith('imgs'):
            print('录制结束。')
            return str(Path(self._path).resolve())

        if not str(self._path).isascii():
            raise ValueError(f'保存路径只能包含英文。当前：{self._path}')

        try:
            from cv2 import VideoWriter, imread, VideoWriter_fourcc
            from numpy import fromfile, uint8  # noqa: F401 —— 同时探测 numpy 是否安装
        except (ImportError, ModuleNotFoundError):
            raise OSError('录制视频需要安装 cv2 和 numpy：\npip install opencv-python\npip install numpy')

        pic_list = Path(self._tmp_path or self._path).glob('*.jpg')
        img = imread(str(next(pic_list)))
        img_info = img.shape
        size = (img_info[1], img_info[0])

        video_write = VideoWriter(path, VideoWriter_fourcc(*coding.upper()), 5, size)

        for i in pic_list:
            img = imread(str(i))
            video_write.write(img)

        rmtree(self._tmp_path)
        self._tmp_path = None
        print('录制结束。')
        return f'{self._path}{sep}{video_name}'

    def set_save_path(self, save_path=None):
        if save_path:
            save_path = Path(save_path)
            if save_path.exists() and save_path.is_file():
                raise ValueError('保存路径必须是文件夹。')
            save_path.mkdir(parents=True, exist_ok=True)
            self._path = save_path

    def _run(self):
        self._running = True
        path = self._tmp_path or self._path
        while self._enable:
            self._owner.get_screenshot(path=path, name=f'{time()}.jpg')
            sleep(.04)
        self._running = False

    def _onScreencastFrame(self, **kwargs):
        path = self._tmp_path or self._path
        with open(f'{path}{sep}{kwargs["metadata"]["timestamp"]}.jpg', 'wb') as f:
            f.write(b64decode(kwargs['data']))
        self._owner._run_cdp('Page.screencastFrameAck', sessionId=kwargs['sessionId'])


class ScreencastMode(object):
    def __init__(self, screencast):
        self._screencast = screencast

    def video_mode(self):
        self._screencast._mode = 'video'

    def frugal_video_mode(self):
        self._screencast._mode = 'frugal_video'

    def js_video_mode(self):
        self._screencast._mode = 'js_video'

    def frugal_imgs_mode(self):
        self._screencast._mode = 'frugal_imgs'

    def imgs_mode(self):
        self._screencast._mode = 'imgs'
