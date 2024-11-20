import requests
import concurrent.futures
import time
import sys
import configparser
import json
import os
import urllib3
import argparse

# Disable warnings
urllib3.disable_warnings(urllib3.exceptions.NotOpenSSLWarning)

# Files for data storage
DOMAINS_FILE = 'domains.txt'
CONFIG_FILE = 'api_config.json'
RESULTS_FILE = 'results.txt'

def load_api_configs():
    """Loading API configurations from JSON file"""
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"File {CONFIG_FILE} not found!")
        return {}

def get_all_zones(api_token):
    """Getting ALL zones (domains) in the account with pagination"""
    headers = {
        'Authorization': f'Bearer {api_token}',
        'Content-Type': 'application/json'
    }

    all_zones = []
    page = 1
    per_page = 50  # Maximum number of domains per page in Cloudflare API

    while True:
        url = f'https://api.cloudflare.com/client/v4/zones?page={page}&per_page={per_page}'
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            
            if data['success']:
                zones = data['result']
                all_zones.extend(zones)
                
                # Check if there are more pages
                total_pages = data['result_info'].get('total_pages', 0)
                
                if page >= total_pages:
                    break
                
                page += 1
            else:
                print("Failed to get zones list")
                break
        except requests.exceptions.RequestException as e:
            print(f"Error getting zones list: {e}")
            break

    return all_zones

def process_domains_for_account(api_token, account_name):
    """Processing domains for a specific account"""
    zones = get_all_zones(api_token)
    print(f"Found domains in account {account_name}: {len(zones)}")
    
    account_results = []
    
    for zone in zones:
        domain = zone['name']
        zone_id = zone['id']
        
        headers = {
            'Authorization': f'Bearer {api_token}',
            'Content-Type': 'application/json'
        }
        
        # Getting A records
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
            print(f"Error getting records for {domain}: {e}")
    
    return account_results

def export_dns_records(api_configs):
    """Export DNS records for all accounts"""
    all_results = []
    
    # Using multithreading for processing accounts
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(api_configs)) as executor:
        # Preparing tasks
        future_to_account = {
            executor.submit(process_domains_for_account, account_data['token'], account_name): account_name 
            for account_name, account_data in api_configs.items()
        }
        
        # Collecting results
        for future in concurrent.futures.as_completed(future_to_account):
            account_results = future.result()
            all_results.extend(account_results)
    
    # Writing results to file
    with open(RESULTS_FILE, 'w') as f:
        f.write("Domain;IP;Account\n")
        f.writelines(all_results)
    
    print(f"DNS records export completed. Results saved in {RESULTS_FILE}")

def get_domain_ip(domain, api_configs):
    """Getting IP for a specific domain"""
    # First searching in local results.txt file
    try:
        with open(RESULTS_FILE, 'r') as f:
            # Skip header
            next(f)
            for line in f:
                parts = line.strip().split(';')
                if len(parts) >= 2 and parts[0].lower() == domain.lower():
                    # Return format: domain - IP (Account: account)
                    account = parts[2] if len(parts) > 2 else "Unknown account"
                    return f"{domain} - {parts[1]} (Account: {account})"
    except FileNotFoundError:
        print(f"File {RESULTS_FILE} not found. Searching via API.")
    except Exception as e:
        print(f"Error reading {RESULTS_FILE}: {e}")

    # If not found in local file, search via API
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
                                return f"{domain} - {record['content']} (Account: {account_name})"
                except requests.exceptions.RequestException:
                    pass
    
    return None

def main():
    # Parsing command line arguments
    parser = argparse.ArgumentParser(description='Cloudflare Domain IP Search')
    parser.add_argument('-d', '--domain', help='Domain to check')
    parser.add_argument('-u', '--update', action='store_true', help='Update domain database')
    
    args = parser.parse_args()

    # Loading API configurations
    api_configs = load_api_configs()
    
    if not api_configs:
        print("No API configurations available!")
        return

    # If arguments are provided, execute corresponding actions
    if args.domain:
        ip = get_domain_ip(args.domain, api_configs)
        if ip:
            print(f"Found: {ip}")
            with open(DOMAINS_FILE, 'a') as f:
                f.write(f"{ip}\n")
        else:
            print(f"IP for {args.domain} not found in any account")
    
    elif args.update:
        start_time = time.time()
        export_dns_records(api_configs)
        print(f"Execution time: {time.time() - start_time:.2f} seconds")
    
    # If no arguments - run interactive mode
    else:
        while True:
            print("\nSelect action:")
            print("1 - Check domain")
            print("2 - Update domain database")
            print("3 - Exit")
            
            choice = input("Enter action number: ")

            if choice == '1':
                domain = input("Enter domain (without www, example: example.com): ").strip()
                
                ip = get_domain_ip(domain, api_configs)
                if ip:
                    print(f"Found: {ip}")
                    with open(DOMAINS_FILE, 'a') as f:
                        f.write(f"{ip}\n")
                else:
                    print(f"IP for {domain} not found in any account")
            
            elif choice == '2':
                start_time = time.time()
                export_dns_records(api_configs)
                print(f"Execution time: {time.time() - start_time:.2f} seconds")
            
            elif choice == '3':
                break
            
            else:
                print("Invalid choice. Try again.")

if __name__ == "__main__":
    main()
