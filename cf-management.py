import requests
import concurrent.futures
import time
import sys
import configparser
import json
import os
import urllib3
import argparse

# Отключаем предупреждения
urllib3.disable_warnings(urllib3.exceptions.NotOpenSSLWarning)

# Файлы для хранения данных
DOMAINS_FILE = 'domains.txt'
CONFIG_FILE = 'api_config.json'
RESULTS_FILE = 'results.txt'

def load_api_configs():
    """Загрузка конфигураций API из JSON файла"""
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Файл {CONFIG_FILE} не найден!")
        return {}

def get_all_zones(api_token):
    """Получение ВСЕХ зон (доменов) в аккаунте с постраничной навигацией"""
    headers = {
        'Authorization': f'Bearer {api_token}',
        'Content-Type': 'application/json'
    }

    all_zones = []
    page = 1
    per_page = 50  # Максимальное количество доменов на страницу в Cloudflare API

    while True:
        url = f'https://api.cloudflare.com/client/v4/zones?page={page}&per_page={per_page}'
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            if data['success']:
                zones = data['result']
                all_zones.extend(zones)
                
                # Проверяем, есть ли еще страницы
                total_pages = data['result_info'].get('total_pages', 0)
                
                if page >= total_pages:
                    break
                
                page += 1
            else:
                print("Не удалось получить список зон")
                break
        except requests.exceptions.RequestException as e:
            print(f"Ошибка при получении списка зон: {e}")
            break

    return all_zones

def process_domains_for_account(api_token, account_name):
    """Обработка доменов для конкретного аккаунта"""
    zones = get_all_zones(api_token)
    print(f"Найдено доменов в аккаунте {account_name}: {len(zones)}")
    
    account_results = []
    
    for zone in zones:
        domain = zone['name']
        zone_id = zone['id']
        
        headers = {
            'Authorization': f'Bearer {api_token}',
            'Content-Type': 'application/json'
        }
        
        # Получаем A записи
        url = f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type=A'
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            if data['success']:
                records = data['result']
                for record in records:
                    if record['type'] == 'A' and (record['name'] == '@' or record['name'] == domain):
                        result = f"{domain};{record['content']};{account_name}\n"
                        account_results.append(result)
        
        except requests.exceptions.RequestException as e:
            print(f"Ошибка при получении записей для {domain}: {e}")
    
    return account_results

def export_dns_records(api_configs):
    """Экспорт DNS записей для всех аккаунтов"""
    all_results = []
    
    # Используем многопоточность для обработки аккаунтов
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(api_configs)) as executor:
        # Подготовка задач
        future_to_account = {
            executor.submit(process_domains_for_account, account_data['token'], account_name): account_name 
            for account_name, account_data in api_configs.items()
        }
        
        # Сбор результатов
        for future in concurrent.futures.as_completed(future_to_account):
            account_results = future.result()
            all_results.extend(account_results)
    
    # Запись результатов в файл
    with open(RESULTS_FILE, 'w') as f:
        f.write("Domain;IP;Account\n")
        f.writelines(all_results)
    
    print(f"Экспорт DNS записей завершен. Результат сохранен в {RESULTS_FILE}")

def get_domain_ip(domain, api_configs):
    """Получение IP для конкретного домена"""
    # Сначала ищем в локальном файле results.txt
    try:
        with open(RESULTS_FILE, 'r') as f:
            # Пропускаем заголовок
            next(f)
            for line in f:
                parts = line.strip().split(';')
                if len(parts) >= 2 and parts[0].lower() == domain.lower():
                    # Возвращаем формат: домен - IP (Аккаунт: account)
                    account = parts[2] if len(parts) > 2 else "Неизвестный аккаунт"
                    return f"{domain} - {parts[1]} (Аккаунт: {account})"
    except FileNotFoundError:
        print(f"Файл {RESULTS_FILE} не найден. Выполняется поиск через API.")
    except Exception as e:
        print(f"Ошибка при чтении {RESULTS_FILE}: {e}")

    # Если в локальном файле не нашли, ищем через API
    for account_name, account_data in api_configs.items():
        api_token = account_data['token']
        
        zones = get_all_zones(api_token)
        
        for zone in zones:
            if zone['name'] == domain:
                zone_id = zone['id']
                
                headers = {
                    'Authorization': f'Bearer {api_token}',
                    'Content-Type': 'application/json'
                }
                
                url = f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type=A'
                try:
                    response = requests.get(url, headers=headers)
                    response.raise_for_status()
                    data = response.json()
                    
                    if data['success']:
                        records = data['result']
                        for record in records:
                            if record['type'] == 'A' and (record['name'] == '@' or record['name'] == domain):
                                return f"{domain} - {record['content']} (Аккаунт: {account_name})"
                except requests.exceptions.RequestException:
                    pass
    
    return None

def main():
    # Парсинг аргументов командной строки
    parser = argparse.ArgumentParser(description='Cloudflare Domain IP Search')
    parser.add_argument('-d', '--domain', help='Домен для проверки')
    parser.add_argument('-u', '--update', action='store_true', help='Обновить базу доменов')
    
    args = parser.parse_args()

    # Загрузка конфигураций API
    api_configs = load_api_configs()
    
    if not api_configs:
        print("Нет доступных API конфигураций!")
        return

    # Если переданы аргументы, выполняем соответствующие действия
    if args.domain:
        ip = get_domain_ip(args.domain, api_configs)
        if ip:
            print(f"Найдено: {ip}")
            with open(DOMAINS_FILE, 'a') as f:
                f.write(f"{ip}\n")
        else:
            print(f"IP для {args.domain} не найден ни в одном аккаунте")
    
    elif args.update:
        start_time = time.time()
        export_dns_records(api_configs)
        print(f"Время выполнения: {time.time() - start_time:.2f} секунд")
    
    # Если аргументов нет - запускаем интерактивный режим
    else:
        while True:
            print("\nВыберите действие:")
            print("1 - Проверить домен")
            print("2 - Обновить базу доменов")
            print("3 - Выйти")
            
            choice = input("Введите номер действия: ")

            if choice == '1':
                domain = input("Введите домен (без www, например: example.com): ").strip()
                
                ip = get_domain_ip(domain, api_configs)
                if ip:
                    print(f"Найдено: {ip}")
                    with open(DOMAINS_FILE, 'a') as f:
                        f.write(f"{ip}\n")
                else:
                    print(f"IP для {domain} не найден ни в одном аккаунте")
            
            elif choice == '2':
                start_time = time.time()
                export_dns_records(api_configs)
                print(f"Время выполнения: {time.time() - start_time:.2f} секунд")
            
            elif choice == '3':
                break
            
            else:
                print("Неверный выбор. Попробуйте снова.")

if __name__ == "__main__":
    main()
