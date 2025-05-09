import random
import json
import sys
import os
import logging
from datetime import datetime, date
from time import localtime
from requests import get, post
from zhdate import ZhDate
from concurrent.futures import ThreadPoolExecutor

# 配置日志记录
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)

class ConfigManager:
    @staticmethod
    def load_config():
        """安全加载配置文件"""
        try:
            with open("config.json", encoding="utf-8") as f:
                config = json.load(f)
                required_keys = ['app_id', 'app_secret', 'weather_key', 'template_id', 'region', 'user']
                if not all(k in config for k in required_keys):
                    raise ValueError("缺少必要配置项")
                return config
        except Exception as e:
            logging.error(f"配置加载失败: {str(e)}")
            sys.exit(1)

class WeatherAPI:
    def __init__(self, api_key):
        self.api_key = api_key
        self.cache = {}
    
    def get_with_retry(self, url, max_retries=3):
        """带重试机制的请求"""
        for _ in range(max_retries):
            try:
                response = get(url, timeout=10)
                return response.json()
            except Exception as e:
                logging.warning(f"请求失败: {str(e)}, 正在重试...")
        return None

    def get_weather(self, region):
        """获取天气信息带缓存"""
        if region in self.cache:
            return self.cache[region]
        
        # 获取位置ID
        location_url = f"https://geoapi.qweather.com/v2/city/lookup?location={region}&key={self.api_key}"
        location_data = self.get_with_retry(location_url)
        
        if not location_data or location_data['code'] != '200':
            raise ValueError("地区查询失败")
        
        location_id = location_data['location'][0]['id']
        
        # 获取天气数据
        weather_url = f"https://devapi.qweather.com/v7/weather/now?location={location_id}&key={self.api_key}"
        weather_data = self.get_with_retry(weather_url)
        
        if not weather_data or weather_data['code'] != '200':
            raise ValueError("天气查询失败")
        
        result = (
            weather_data['now']['text'],
            f"{weather_data['now']['temp']}°C",
            weather_data['now']['windDir']
        )
        self.cache[region] = result
        return result

class DateCalculator:
    @staticmethod
    def get_color():
        """生成随机颜色"""
        return "#{:06x}".format(random.randint(0, 0xFFFFFF))

    @staticmethod
    def calculate_days(start_date, end_date):
        """计算日期差"""
        return (end_date - start_date).days

    @staticmethod
    def parse_date(date_str):
        """解析日期字符串"""
        if date_str.startswith('r'):
            parts = date_str[1:].split('-')
            return ZhDate(int(parts[0]), int(parts[1]), int(parts[2])).to_datetime().date()
        return datetime.strptime(date_str, "%Y-%m-%d").date()

class WeChatService:
    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self.access_token = None
        self.token_expire = 0

    def get_access_token(self):
        """带缓存的AccessToken获取"""
        if datetime.now().timestamp() < self.token_expire:
            return self.access_token
        
        url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={self.app_id}&secret={self.app_secret}"
        response = get(url).json()
        
        if 'access_token' not in response:
            logging.error(f"获取Token失败: {response}")
            raise ValueError("微信API认证失败")
        
        self.access_token = response['access_token']
        self.token_expire = datetime.now().timestamp() + response['expires_in'] - 300
        return self.access_token

    def send_template_message(self, user_id, template_data):
        """发送模板消息"""
        url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={self.get_access_token()}"
        response = post(url, json=template_data)
        result = response.json()
        
        if result.get('errcode') != 0:
            logging.error(f"消息发送失败: {result}")
            return False
        return True

class MessageGenerator:
    def __init__(self, config):
        self.config = config
        self.weather_api = WeatherAPI(config['weather_key'])
        self.date_calculator = DateCalculator()
        self.wechat_service = WeChatService(config['app_id'], config['app_secret'])

    def generate_message_data(self):
        """生成消息内容"""
        today = datetime.now().date()
        week_list = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"]
        
        try:
            weather, temp, wind_dir = self.weather_api.get_weather(self.config['region'])
        except Exception as e:
            logging.error(f"天气查询失败: {str(e)}")
            weather = temp = wind_dir = "未知"

        # 基础数据
        base_data = {
            "date": {
                "value": f"{today} {week_list[today.weekday()]}",
                "color": self.date_calculator.get_color()
            },
            "region": {
                "value": self.config['region'],
                "color": self.date_calculator.get_color()
            },
            "weather": {"value": weather, "color": self.date_calculator.get_color()},
            "temp": {"value": temp, "color": self.date_calculator.get_color()},
            "wind_dir": {"value": wind_dir, "color": self.date_calculator.get_color()}
        }

        # 处理纪念日
        anniversary_data = {}
        for idx, ann in enumerate(self.config.get('anniversaries', [])):
            start_date = self.date_calculator.parse_date(ann['date'])
            days = self.date_calculator.calculate_days(start_date, today)
            anniversary_data[f'anniversary_{idx}'] = {
                "value": f"{ann['name']}已经 {days} 天",
                "color": self.date_calculator.get_color()
            }

        # 处理生日
        birthday_data = {}
        for idx, bd in enumerate(self.config.get('birthdays', [])):
            birth_date = self.date_calculator.parse_date(bd['date'])
            next_birthday = birth_date.replace(year=today.year)
            if next_birthday < today:
                next_birthday = next_birthday.replace(year=today.year + 1)
            days = (next_birthday - today).days
            status = "今天生日！" if days == 0 else f"还有{days}天"
            birthday_data[f'birthday_{idx}'] = {
                "value": f"{bd['name']}生日{status}",
                "color": self.date_calculator.get_color()
            }

        return {**base_data, **anniversary_data, **birthday_data}

    def send_messages(self):
        """并发发送消息"""
        template_data = {
            "template_id": self.config['template_id'],
            "url": self.config.get('redirect_url', 'http://weixin.qq.com/download'),
            "topcolor": "#FF0000",
            "data": self.generate_message_data()
        }

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for user in self.config['user']:
                future = executor.submit(
                    self.wechat_service.send_template_message,
                    user,
                    {"touser": user, **template_data}
                )
                futures.append(future)
            
            results = [f.result() for f in futures]
            logging.info(f"成功发送 {sum(results)} 条消息，失败 {len(results)-sum(results)} 条")

if __name__ == "__main__":
    try:
        config = ConfigManager.load_config()
        generator = MessageGenerator(config)
        generator.send_messages()
    except Exception as e:
        logging.error(f"程序异常终止: {str(e)}")
        sys.exit(1)
