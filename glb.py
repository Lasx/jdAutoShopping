from ruamel import yaml
import logging
import re

import atexit
import time
from requests import Timeout, TooManyRedirects

import account

# 设置日志
logging.getLogger('urllib3').setLevel(logging.FATAL)
logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s', level=logging.INFO)
configFileName = './config.yaml'
reqHeaders = {
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/77.0.3835.0 Safari/537.36',
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'accept-language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
    'accept-encoding': 'gzip, deflate',
    'DNT': '1',
    'connection': 'keep-alive',
    'Pragma': 'no-cache',
    'cache-control': 'no-cache',
    'TE': 'Trailers',
    'X-Requested-With': 'XMLHttpRequest'
}

GET = 'GET'
POST = 'POST'


def canBuy(itemId):
    return items[itemId]['inStock'] and not items[itemId]['snappingUp']


with open(configFileName) as file:
    config = yaml.round_trip_load(file)
# 运行时记录有无货
items = {itemId: {'inStock': False,
                  'snappingUp': False} for itemId in config['items'].keys()}

accountDict: dict = {}
for _id, _config in config['accounts'].items():
    # remove unASCII char
    for _key, _value in tuple(_config['cookies'].items()):
        if re.search(r'[^\u0000-\u007F]', _value) is not None:
            del _config['cookies'][_key]
    accountDict[_id] = account.Account(_id, _config)


def saveConfig():
    with open(configFileName, 'w') as _file:
        for _id, _account in accountDict.items():
            config['accounts'][_id]['cookies'] = _account.sess.cookies.get_dict()
        yaml.round_trip_dump(config, _file, indent=4)


atexit.register(saveConfig)
accountList = list(accountDict.values())
_currAccountIndex = 0
defaultLogLvl = 0
successLogLvl = 1
redirectLogLvl = 2
timeoutLogLvl = 3
tooManyFailureLogLvl = 4

continueReq = 0


def request(
        actionName, sess, method, url, params=None, headers={}, cookies=None, data=None,
        checkFuc=lambda _resp, args: False, args=(),
        redirect=True, logLvl={}, timeout=2):
    _defaultLogLvl = logLvl[defaultLogLvl] if defaultLogLvl in logLvl else logging.WARNING
    _successLogLvl = logLvl[successLogLvl] if successLogLvl in logLvl else _defaultLogLvl - 10
    _redirectLogLvl = logLvl[redirectLogLvl] if redirectLogLvl in logLvl else _defaultLogLvl - 10
    _timeoutLogLvl = logLvl[timeoutLogLvl] if timeoutLogLvl in logLvl else _defaultLogLvl
    _tooManyFailureLogLvl = logLvl[tooManyFailureLogLvl] if tooManyFailureLogLvl in logLvl else _defaultLogLvl
    sleepTime = 0.5
    attemptTimes = 10
    while attemptTimes > 0:
        attemptTimes -= 1
        # sleepTime += 0.5
        # 声明
        resp = None
        try:
            # 使用账户列表中的 session
            if sess is None:
                global _currAccountIndex
                sess = accountList[_currAccountIndex].sess
                if _currAccountIndex == len(accountList) - 1:
                    _currAccountIndex = 0
                else:
                    _currAccountIndex += 1
            resp = sess.request(
                method, url, params, data,
                headers={'Host': re.search('https?://(.*?)(/|$)', url).group(1),
                         **headers},
                cookies=cookies,
                timeout=timeout,
                allow_redirects=False)
            if 'Location' in resp.headers:
                logging.log(_redirectLogLvl, '从 {} 重定向至 {}'.format(url, resp.headers['Location']))
                if '//trade.jd.com/orderBack.html' in resp.headers['Location']:
                    return None
                if redirect:
                    url = resp.headers['location']
                    # headers['Referer'] =
                    continue
                else:
                    return resp
            if 200 <= resp.status_code < 300:
                logging.log(_successLogLvl, '{} 请求成功'.format(actionName))
            elif 400 <= resp.status_code < 500:
                logging.log(_defaultLogLvl,
                            '\n\t'.join(('{} 发生客户端错误'.format(actionName), str(resp.status_code))))
                attemptTimes -= 3
                time.sleep(sleepTime)
                continue
            elif 500 <= resp.status_code < 600:
                logging.log(_defaultLogLvl, '\n'.join(('{} 响应状态码为 500'.format(actionName), resp.text)))
                continue
            else:
                logging.log(_defaultLogLvl, '\n\t'.join(('{} 响应状态码为 {}'.format(actionName, resp.status_code),
                                                         str(resp.headers), resp.text)))
            if checkFuc(resp, args):
                attemptTimes -= 3
                continue
            return resp
        except Timeout:
            logging.log(_timeoutLogLvl, '{} 超时'.format(actionName))
            continue
        except TooManyRedirects:
            logging.log(_defaultLogLvl, '{} 重定向次数过多'.format(actionName))
            return None
        except Exception as e:
            if resp is None:
                logging.log(_defaultLogLvl, '{} 失败, 无 Response'.format(actionName))
            else:
                logging.log(_defaultLogLvl, '\n\t'.join(('{} 失败'.format(actionName), str(resp.status_code),
                                                         str(resp.headers), resp.text)))
            logging.exception(e)
            continue
    else:
        logging.log(_tooManyFailureLogLvl, '{} 失败次数过多'.format(actionName))
        return None
