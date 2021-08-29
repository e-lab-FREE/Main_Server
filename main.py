#!/usr/bin/python3

import socket 
import threading
import json
import datetime
import os
import re
import collections.abc
from time import sleep
from queue import Queue

import logging
from flask import Flask, request, jsonify, session, Response
# from flask_session import Session
# from flask_csv import send_csv
from flask_cors import CORS
app = Flask(__name__)
app.secret_key = '*}6Ttt)G7X_T}3VF:ygc'


app.config['SESSION_TYPE'] = 'filesystem'
# app.config['SESSION_TYPE'] = 'memcached'
#sess = Session(app)
    

app.debug = False
#app.logger.disabled = False



logging.basicConfig(level=logging.DEBUG) # CRITICAL, ERROR, WARNING, INFO, and DEBUG 

log = logging.getLogger('werkzeug')
log.disabled = True

CORS(app)

PORT = 5050
BINARY_DATA_PORT = 5051
SERVER = '192.168.1.102'#10.7.0.1'
DISCONNECT_MESSAGE = '!DISCONNECT'
FORMAT='utf-8'
HEADER=64
BINARY_DATA_LOCATION_BASE = "~/EXP_SERVER_DATA/datafile"
HELPER_DATA = {}
FLAG = 0
c = threading.Condition()
q = Queue()

#ITERADOR PARA DICIONARIOS PROTEGIDOS COM RW LOCK
class Protected_Dict_Iterator:
    def __init__(self, pdict, read_lock, read_unlock):
        #Local pointer to protected_dict and locks
        self.__dict = pdict
        self.__read_lock = read_lock
        self.__read_unlock = read_unlock
        #Create local dict iterator
        self.__interator = self.__dict.__iter__()
        #I'm now reading from the dict, dict must be read locked
        self.__read_lock()
        self.__lock_counter = 1
    def __next__(self):
        #Call iterator until it raises StopIteration
        try:
            return self.__interator.__next__()
        except Exception as e:
            if self.__lock_counter == 1:
                self.__read_unlock()
                self.__lock_counter = 0
            raise e from None
    def __del__(self):
        #I'm done with this iterator. No more reading. Release read_lock
        if self.__lock_counter == 1:
            self.__read_unlock()

#DICIONARIO PROTEGIDO COM RW LOCK
class Protected_Dict(collections.abc.MutableMapping):
    def __init__(self):
        self.__dict_readers = 0
        self.__dict_writers = 0
        self.__control_lock = threading.Lock()
        self.__dict_lock = threading.Lock()
        self.__readers_wait_point = threading.Condition(self.__control_lock)
        self.__writers_wait_point = threading.Condition(self.__control_lock)
        self.__dict = {}

    #Definição interna das funções de rw lock

    def __write_lock(self):
        #Bloqueio outros acessos à lógica de controlo
        self.__control_lock.acquire()
        #Registo-me como +1 a querer escrever
        self.__dict_writers+=1
        #Espero que todos os leitores acabem
        if self.__dict_readers != 0:
            self.__writers_wait_point.wait()
        #Registei-me como escritor e acabei de esperar pelos leitores
        #Solto a lógica de controlo
        self.__control_lock.release()
        #Fico à espera de poder aceder à lista para escrever
        self.__dict_lock.acquire()

    def __write_unlock(self):
        #Acabei as operações na lista, solto o lock
        self.__dict_lock.release()
        #Bloqueio acessos à lógica de controlo
        self.__control_lock.acquire()
        #Retiro-me da contagem de escritores
        self.__dict_writers -= 1
        #Se mais ninguém quiser escrever
        if self.__dict_writers == 0:
            #Solto as threads à espera de ler
            self.__readers_wait_point.notify_all()
        #Desbloqueio acesso à lógica de controlo   
        self.__control_lock.release()
    
    def __read_lock(self):
        #Bloqueio acesso à lógica de controlo
        self.__control_lock.acquire()
        #Se houver threads a escrever ou querer escrever
        if self.__dict_writers != 0:
            #Esperar para ler
            self.__readers_wait_point.wait()
        #Posso ler, registo-me como leitor
        self.__dict_readers += 1
        #Liberto a lógica de controlo
        self.__control_lock.release()

    def __read_unlock(self):
        #Bloqueio acesso à lógica de controlo
        self.__control_lock.acquire()
        #Removo-me da contagem de leitores
        self.__dict_readers -= 1
        #Se não houver mais leitores
        if self.__dict_readers == 0:
            #solto os escritores
            self.__writers_wait_point.notify_all()
        #Liberto a lógica de controlo
        self.__control_lock.release()

    #Overload dos métodos normais da lista;
    #Mas com escritas e leituras protegidas pelo rw lock

    def __iter__(self):
        return Protected_Dict_Iterator(self.__dict, self.__read_lock, self.__read_unlock)

    def __len__(self):
        self.__read_lock()
        value = self.__dict.__len__()
        self.__read_unlock()
        return value
    
    def __getitem__(self,key):
        self.__read_lock()
        print(key)
        try:
            value = self.__dict.__getitem__(key)
            self.__read_unlock()
            return value
        except Exception as e:
            self.__read_unlock()
            raise e
    
    def __setitem__(self,key,value):
        self.__write_lock()
        try:
            self.__dict.__setitem__(key,value)
            self.__write_unlock()
        except Exception as e:
            self.__write_unlock()
            raise e from None

    def __delitem__(self,key):
        self.__write_lock()
        try:
            self.__dict.__delitem__(key)
            self.__write_unlock()
        except Exception as e:
            self.__write_unlock()
            raise e from None

    def __contains__(self,key):
        self.__read_lock()
        value=self.__dict.__contains__(key)
        self.__read_unlock()
        return value

    def __str__(self):
        self.__read_lock()
        value = self.__dict.__str__()
        self.__read_unlock()
        return value

    def __repr__(self):
        self.__read_lock()
        value = self.__dict.__repr__()
        self.__read_unlock()
        return value
        
#EXP_CONN_LIST é uma instancia de protected_list
#E ITERAVEL, MAS ENQUANTO ESTA A ITERAR FAZ READ LOCK NA LISTA
#NAO DEMORAR ETERNIDADES A ITERAR
EXP_CONN_LIST = Protected_Dict()
EXP_PROCOL = Protected_Dict()

#SERVER = socket.gethostbyname(socket.gethostname())

#Acho que isto devia estar no start()
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR,1)
server.bind((SERVER, PORT))

binary_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
binary_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
binary_server.bind((SERVER, BINARY_DATA_PORT))

segredos = { "10.7.0.35":{"segredo":"sou eu","nome":"WP_LIS_IST"}, "192.168.1.83":{"segredo":"estou bem","nome":"Monte_Carlo"}}

def send(msg,conn):
    try:
        message = msg.encode(FORMAT)
        msg_length = len(message)
        send_length = str(msg_length).encode(FORMAT)
        send_length += b' ' * (HEADER - len(send_length))
        conn.sendall(send_length) 
        conn.sendall(message)
    except socket.error:
        raise socket.error

#def receive(conn):
#   msg_length = conn.recv(HEADER)
#    print('\n')
#    print('\n')
#    print(msg_length)
#    print('\n')
#    print('\n')
#    if msg_length:
#        msg_length = int(msg_length)
#        msg = conn.recv(msg_length).decode(FORMAT)
#        myjson = json.loads(msg)
#        if myjson.get('msg_id')!=None:
#            check_msg(myjson)
#        if myjson.get('reply_id')!=None:
#            check_reply(myjson)

def get_Config(Exp):
    #Apanhar erro se ficheiro no existir
    config = 'Configs/'+str(Exp)+'.json'
    with open(config) as json_file:
        data = json.load(json_file)
    return data

def ConfigureRP(conn, id_exp):
    #data = json.dumps(get_Config(segredos[id_exp]['nome']))
    # print(data)
    # print(type(data))
    data = get_Config(segredos[id_exp]['nome'])
    data_1 = json.dumps(data)

    
    send_mensage ='{"msg_id": "1","config_file":'+str(data_1)+'}'
    print(send_mensage)
    send(send_mensage,conn)
    global EXP_PROCOL
    EXP_PROCOL[segredos[id_exp]['nome']] = '{"protocols":'+str(data['protocols']).replace('\'','"')+'}'


def ConfigureStartExperiment(user_json):
    #VALIDAR CONFIG
    print(user_json)
    verificar = []
    conn = EXP_CONN_LIST[user_json['experiment_name']]
    exp_config_json = user_json['config_experiment']

    if 'protocol' in user_json:
        protocol = int(user_json['protocol'])
    else:
        protocol = 0

    tester = json.loads(EXP_PROCOL[user_json['experiment_name']])
    print('Limites: '+str(tester['protocols'][protocol]['exp_paremeters']))
    print('\n')
    print('Tamanho do que o config tem guardaddo: '+str(len(tester['protocols'][protocol]['exp_paremeters'])))
    print (user_json)
    print('Tamanho do que o user mandou: '+str(len(exp_config_json.keys())))
    print('\n')
    if len(tester['protocols'][protocol]['exp_paremeters']) == len(exp_config_json.keys()):
        print('\nO número de parametros enviado pelo user esta de acordo com a config! \n')
        print('\nA iniciar verificação de limites! \n')
        for i in range(0,len(exp_config_json.keys())):
            min = int(tester['protocols'][protocol]['exp_paremeters'][i]['min_val'])
            max = int(tester['protocols'][protocol]['exp_paremeters'][i]['max_val'])
            if min <= int(exp_config_json[list(exp_config_json.keys())[i]]) and  max >= int(exp_config_json[list(exp_config_json.keys())[i]]):
                print ('Esta dentro dos limites a variavel '+ str(list(exp_config_json.keys())[i]))
                print ('\n')
                verificar.append(True)
            elif min > int(exp_config_json[list(exp_config_json.keys())[i]]):
                print('erro')
                exp_config_json[list(exp_config_json.keys())[i]] = str(min)
                verificar.append(False)
            else:
                print('erro')
                exp_config_json[list(exp_config_json.keys())[i]] = str(max)
                exp_config = json.dumps(exp_config_json)
                verificar.append(False)
        if all(verificar) == False:
            exp_config = json.dumps(exp_config_json)
            send_mensage = '{"msg_id": "2","error":"-1","config_params":'+str(exp_config)+'}'
        else:
            exp_config = json.dumps(exp_config_json)
            send_mensage = '{"msg_id": "2","config_params":'+str(exp_config)+'}'
        
        print(send_mensage)

        #Send to target experiment server
        send(send_mensage,conn)
        sleep(0.000001)
    else:
        # Não sei bem o que fazer quando isto acontece quando o numero de parametros que o user manda são diferentes do que se esta a espera 
        # A meu ver deviamos ter no config json la um valor default.
        # O que fazer quando estão a mais ignorar os que estão a mais ?
        # Mas acho que nem deviamos mandar para a frente a mensagem nestes casos deviamos é informar o flask que fez pop
        print('Número de parametos errado por favor verifique a config que mandou!!\n')
    #Prepare string to be sent. Format so that it can be transformed back to json at the receiver
    
   

    #receive(conn)

    # #Receive response length
    # msg_length = conn.recv(HEADER)
    # #Receive reply
    # msg = conn.recv(msg_length).decode(FORMAT)

    # #Format received reply as json
    # reply_json = json.loads(msg)

    #CHECK REPLY! (Alterações na função check_reply)

@app.route('/user', methods=['POST'])
def Flask_f1():
    if request.method == 'POST':
        #origin = request.headers.get('Origin')	
        print(request.data)
        user_json = json.loads(request.data.decode(FORMAT))
        ConfigureStartExperiment(user_json)

        return '' #jsonify({'JSON Enviado' : request.args.get('JSON'), 'result': 'OK!'})




def StopCurrentExperiment(conn):
    print("A enviar mensagem com pedido para para experiencia\n")
    send_message = '{"msg_id":"3"}'
    send(send_message,conn)
    #CHECK_REPLY

def Reset(conn):
    print("A enviar mensagem com pedido para dar reset na experiencia")
    send_message = '{"msg_id":"4"}'
    send(send_message,conn)
    #CHECK_REPLY

def GetCurrentExperimentStatus(conn):
    print("A enviar mensagem com pedido sobre estado da experiencia")
    send_message = '{"msg_id":"5"}'
    send(send_message,conn)
    #CHECK_REPLY

def check_Experiment(id_Exp, segredo,conn):
    #print(segredos.get(id_Exp)!=None)
    
    # Testa se o ID existe no safe das experiencias que existem.
    if segredos.get(id_Exp)!=None:
        # Testa se o segredo esta correcto
        if (segredo == segredos[id_Exp]['segredo']):
            print('A experiencia '+ segredos[id_Exp]['nome'] + ' ('+id_Exp +') foi conectada')
            global EXP_CONN_LIST
            EXP_CONN_LIST[segredos[id_Exp]['nome']] = conn
            print(' ')
            print(' Print dict : \n\n')
            print(EXP_CONN_LIST)
            print(' ')
            print(' ')
            return 0
        return -1
    else:
        return -2

def check_msg(myjson,conn):
    msg_id = myjson['msg_id']
    #ACHO QUE NUNCA RECEBE msg_id = 2. Este if não é preciso
    if (msg_id == '2'):
        # conn_1 = EXP_CONN_LIST[myjson['experiment_name']] 
        # exp_config_json = myjson['config_experiment']
        # ^ isto foi para dentro da função ConfigureStartExperiment() assim a pessoa que nos vai fazer o front end so tem 
        # de mandar um json com o {'experiment_name':'Pendulo' ,'config_experiment':'...' }

        # Penso que assim é mais realista com o que vai ser no final, é verdade que nunca vamos usar o mensagem id 2 
        # para o server_Exp mas para esta faze de teste é necessario para testar a função que depois vai ser chamada no flask
        ConfigureStartExperiment(myjson)
    elif(msg_id == '6'):
        print("Recebi mensagem com pedido de configuracao\n")
        msg_erro = check_Experiment(myjson['id_RP'], myjson['segredo'],conn)
        if (msg_erro == 0):
            print ('reply_id = 6, Comunicação: Conecção estabelecida.')
            send_mensage ='{"reply_id": "6", "status":"0","info": "Segredo correcto. Bem vindo ao e-lab!"}'
            send(send_mensage,conn)
            ConfigureRP(conn,myjson['id_RP'])
        elif (msg_erro == -1):
            print ('msg_id = 6, ERROR: Segredo incorreto.')
            send_mensage ='{"reply_id": "6", "status":"-1","error": "Segredo incorreto.", "nota":"Verifique se os ficheiros de autenticação estao atualizados."}'
            send(send_mensage,conn)
        elif (msg_erro == -2):
            print ('msg_id = 6, ERROR: Experiencia não existe na data base.')
            send_mensage ='{"reply_id": "6", "status":"-2","error": "Experiencia não existe na data base.", "nota":"Contacte o E-lab a experiencia ainda não deve estar registada."}'
            send(send_mensage,conn)
        return True
    elif(msg_id == '7'):
        #print ("msg_id = "+msg_id+ 'Results received: '+str(myjson['results']))
        return True
    elif(msg_id == '8'):
        print ("msg_id = "+msg_id+ 'Error ID:'+myjson['error']+' Status: '+myjson['status'])
        return True
    elif(msg_id == '9'):
        print ("msg_id = "+msg_id+ 'Timestamp: '+ myjson['timestamp'] +'Status of Exp: '+myjson['experiment_status']+ 'and current config: '+myjson['current_config'])
        return True
    elif(msg_id == '10'):
        print ("msg_id = "+msg_id+ 'Timestamp: '+ myjson['timestamp'] +'The data is in: '+myjson['id_dados_bin'])
        return True
    elif(msg_id == '11'):
        print("Recebi dados da experiencia")
        # global HELPER_DATA
        # c.acquire()
        # HELPER_DATA = myjson
        # c.notify_all()
        # c.release()
        global q
        q.put(myjson)
        if str(myjson["status"]) == "Experiment Ended":
            print("Experiment ended at the time: "+str(myjson["timestamp"])+"\n") 
            # printar a varivel global 
        elif str(myjson["status"]) == "running":
            #pass
            print("Time:"+str(myjson["timestamp"])+";\n Status:"+str(myjson["status"])+";\n Dados:"+str(myjson['Data'])+"\n")
            # Gravar numa variavel global todos os dados
        else:
            print("Json is incorrect verify the RPi_Server of the experemente ")
        
    else:
        return False

@app.route('/resultpoint', methods=['GET'])
def getPoint():
    # global HELPER_DATA
    # c.acquire()
    # c.wait()
    global q
    send_data = q.get()
    q.task_done()
    #HELPER_DATA = {}
    # c.release()
    print(send_data)
    return send_data


def check_reply(myjson):
        reply_msg = myjson['reply_id']

        if(reply_msg == '1'):
            if 'error' in myjson :
                print ("Error in the reply_id: "+reply_msg+ 'type of error: '+ myjson['error']+ ' Status: ' + myjson['status'])
            else:
                print ("reply_id = "+reply_msg+ ' Status: ' + myjson['status'])
            return True
        elif(reply_msg == '2'):
            if 'error' in myjson :
                print ("Error in the reply_id: "+reply_msg+ 'type of error: '+ myjson['error']+ ' Status: ' + myjson['status'])
            else:
                print ("reply_id = "+reply_msg+ ' Status: ' + myjson['status'])
            return True
        elif(reply_msg == '3'):
            if 'error' in myjson :
                print ("Error in the reply_id: "+reply_msg+ 'type of error: '+ myjson['error']+ ' Status: ' + myjson['status'])
            else:
                print ("reply_id = "+reply_msg+ ' Status: ' + myjson['status'])
            return True
        elif(reply_msg == '4'):
            if 'error' in myjson :
                print ("Error in the reply_id: "+reply_msg+ 'type of error: '+ myjson['error']+ ' Status: ' + myjson['status'])
            else:
                print ("reply_id = "+reply_msg+ ' Status: ' + myjson['status'])
            return True
        elif(reply_msg == '5'):
            if 'error' in myjson :
                print ("Error in the reply_id: "+reply_msg+ 'type of error: '+ myjson['error']+ ' Status: ' + myjson['status'])
            else:
                print ("reply_id = "+reply_msg+ ' Status: ' + myjson['status'])
            return True
        else: #INVALID MESSAGE
            return False

def handle_Experiments(conn,addr):
    #print (f"[New Connection] {addr} connected")
    while True:
        try:
            msg_length = conn.recv(HEADER, socket.MSG_WAITALL)
            if msg_length == b'':
                raise socket.error
            else:
                msg_length = int(msg_length)
                msg = conn.recv(msg_length, socket.MSG_WAITALL).decode(FORMAT)
                if msg == b'':
                    raise socket.error
                else:
                    if msg == DISCONNECT_MESSAGE:
                        #Sera abusivo? Ao fazer raise de erro estou a sair como se houvesse erro
                        #mas o disconnect foi limpo
                        raise socket.error
                    # print(msg+"\n")
                    myjson = json.loads(msg)
                    if 'msg_id' in myjson:
                        check_msg(myjson,conn)
                    elif 'reply_id' in myjson:
                        check_reply(myjson)
                    else:
                        print("Wrong format of mensage\n")
                    # print("msg_id = "+myjson['msg_id'])
        except socket.error as e:
            #LOG_ERROR
            print('Exp: '+str(e))
            for key,value in EXP_CONN_LIST.items():
                if value == conn :
                    marker = key
            del EXP_CONN_LIST[marker]
            del EXP_PROCOL[marker]
            conn.shutdown(socket.SHUT_RDWR)
            conn.close()
            #Cliente desligou-se ou ligação caiu
            #Esta thread acabou
            return

#Para chamar na função open(). (alegamente) Cria ficheiros de forma atómica (threadsafe)
#Isto está martelado. Mais fácil usar um mutex para cada thread criar um ficheiro de nome diferente
def file_opener(path, flags):
    return os.open(path, flags | os.O_CREAT | os.O_EXCL)

def binary_data_service():
   
    binary_server.listen()
    while True:
        conn, addr = binary_server.accept()

        data_size = conn.recv(HEADER) #verificar se o valor e invalido
        bin_data = conn.recv(data_size) #verificar se foi tudo recebido

        suffix = datetime.datetime.now().strftime("%y%m%d_%H%M%S") #determinar a hora e escrever numa string
        while True:
            try: 
                bin_data_fp = open(BINARY_DATA_LOCATION_BASE+suffix,"wb", opener=file_opener)
            
            except:
                suffix = datetime.datetime.now().strftime("%y%m%d_%H%M%S") #determinar de novo a hora e tentar de novo
                continue

            else:
                break
        
        bin_data_fp.write(bin_data) #APANHAR POSSIVEIS ERROS AQUI
        bin_data_fp.close()
        #RESPONDER COM SUCESSO/ERRO E REFERENCIA DO FICHEIRO
        conn.shutdown(socket.SHUT_RDWR)
        conn.close()

def print_help():
    print("Só é possível enviar comandos do tipo do pêndulo\n\
        Formato dos comandos:\n\
        Nome_exp cmd (deltaX:int sample:int)\n\
        Os comandos aceites são: stp, cfg, rst, ids\n\
        No caso enviar o comando cfg é necessário enviar os parâmetros e os respectivos valores\n\
        e.g.: WP_LIS_IST cfg deltaX:25 samples:100\n")

#cfg deltaX[3:22] N[2:500]
#https://regex101.com/
#para caçar o nome da exp e o comando -> r"^(\D+)\s(\D+)"
#em caso de cfg, para caçar os pares $parametro:$valor -> r"(\w+):(\d+)"
def local_command_func():
    global EXP_CONN_LIST
    
    re_name_cmd = re.compile("^(?P<experiment_name>\w+)\s(?P<experiment_command>\w+)")
    re_parameters = re.compile("(\w+):(\d+)")
    while True:
        cmd = input("Please insert command to send to a experiment\n")
        cmd = cmd.strip()
        if re_name_cmd.match(cmd) != None :
            re_match = re_name_cmd.match(cmd)
            if re_match.group("experiment_command") == "cfg" and re_match.group("experiment_name") in EXP_CONN_LIST:
                #Aqui para ser mais completo, ler do json correspondente
                #os parametros que a experiencia tem, se for para funcionar com
                #outras para além do pêndulo
                tester = json.loads(EXP_PROCOL[re_match.group("experiment_name")])
                samples = None
                deltaX = None
                paramenters_found = re_parameters.findall(cmd)
                try:
                   
                    for paramater in paramenters_found:
                        if paramater[0] == tester["protocols"][0]['exp_paremeters'][0]['nome']:
                            deltaX = int(paramater[1])
                        elif paramater[0] == tester["protocols"][0]['exp_paremeters'][1]['nome']:
                            samples = int(paramater[1])
                except:
                    pass
                if (deltaX and samples) != None:
                    print("Comando de configuração recebido. A enviar para experiência")
                    my_config = json.loads( '{"experiment_name":"'+str(re_match.group("experiment_name"))+'","config_experiment":{"'+str(tester["protocols"][0]['exp_paremeters'][0]['nome'])+'":'+str(deltaX)+',"'+str(tester["protocols"][0]['exp_paremeters'][1]['nome'])+'":'+str(samples)+'} }')
                    ConfigureStartExperiment(my_config)
                else:
                    #comando mal formatado
                    print_help()
            elif re_match.group("experiment_command") == "stp":
                    print("Comando de stop recebido. A enviar para experiência")
                    exp_conn = EXP_CONN_LIST[re_match.group("experiment_name")]
                    StopCurrentExperiment(exp_conn)
                #find experiment in socket list
                #send stop command
            elif re_match.group("experiment_command") == "rst":
                    print("Comando de reset recebido. A enviar para experiência")
                    exp_conn = EXP_CONN_LIST[re_match.group("experiment_name")]
                    Reset(exp_conn)
                #find experiment in socket list
                #send reset command
            elif re_match.group("experiment_command") == "ids":
                print("Comando de status recebido. A enviar para experiência")
                exp_conn = EXP_CONN_LIST[re_match.group("experiment_name")]
                GetCurrentExperimentStatus(exp_conn)
                #find experiment in socket list
                #send reset command
            else:
                #string mal formatada
                print_help()
        elif cmd == "?":
            print_help()

def flask_ready():
    app.run('192.168.1.102',8001,debug=False)


def start():
    flask_server_thread = threading.Thread(target=flask_ready)
    flask_server_thread.start()
    binary_data_server_thread = threading.Thread(target=binary_data_service)
    binary_data_server_thread.start()
    # mandar codigos pela linha de comando
    local_command_thread = threading.Thread(target=local_command_func)
    local_command_thread.start()
    server.listen()
    while True:
        conn,addr = server.accept()
        thread = threading.Thread(target=handle_Experiments,args=(conn,addr),daemon=True)
        thread.start()
        #Isto e -3 porque o programa base tem 3 threads a correr:
        #- A que espera por ligacoes
        #- A do servico binario
        #- A do receber comandos pela linha de comandos
        #print(f"[Active Connections] # = {threading.activeCount()-3}")

# get_Config()
if __name__ == "__main__":
    print("[Starting] Experiment Server Starting...")
    # app.run()
    start()


# with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
#     s.bind((SERVER, PORT))
#     s.listen()
#     conn, addr = s.accept()
#     with conn:
#         print('Connected by', addr)
#         while True:
#             data = conn.recv(1024)
#             if not data:
#                 break
#             conn.sendall(data)
