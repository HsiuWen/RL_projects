import sys
import os
import argparse
import random
import math
import numpy as np
from collections import namedtuple, deque
import matplotlib.pyplot as plt
import cv2
import gym
from gym import wrappers

import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
import torchvision.transforms as T
from torchvision import transforms
from tensorboardX import SummaryWriter

# if gpu is to be used
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

### Classes to deal replay the game for training
Transition = namedtuple('Transition',
                        ('state', 'action', 'next_state', 'reward'))

class ReplayMemory(object):

    def __init__(self, capacity):
        self.memory = deque([],maxlen=capacity)

    def push(self, *args):
        """Save a transition"""
        self.memory.append(Transition(*args))

    def sample(self, batch_size):
        return random.sample(self.memory, batch_size)

    def __len__(self):
        return len(self.memory)

class CNN_2c2f(nn.Module):
    def __init__(self):
        super(CNN_2c2f, self).__init__()
        self.conv1 = nn.Conv2d(4,16,8,stride=4) #output will be 20x20 feature
        self.conv2 = nn.Conv2d(16,32,4,stride=2) #output will be 9x9
        self.fc1 = nn.Linear(32*81,256)
        self.fc2 = nn.Linear(256,6)

    def forward(self,x):

        #pdb.set_trace()
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = x.view(-1,32*81)
        x = F.relu(self.fc1(x))
        x = self.fc2(x)
        return x


class LinearQN(nn.Module):
    def __init__(self, n_in, n_out):
        super(LinearQN, self).__init__()
        self.fc = nn.Linear(n_in, n_out)

    def forward(self, x):
        x = self.fc(x)
        return x

class DQN(nn.Module):
    def __init__(self, n_in, n_hidden, n_out):
        super(DQN, self).__init__()
        self.fc1 = nn.Linear(n_in, n_hidden)
        self.fc2 = nn.Linear(n_hidden, n_hidden)
        self.fc3 = nn.Linear(n_hidden, n_hidden)
        self.fc4 = nn.Linear(n_hidden, n_out)

    def forward(self, x):
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = F.relu(self.fc3(x))
        x = self.fc4(x)
        return x

class DuelingDQN(nn.Module):
    def __init__(self, n_in, n_hidden, n_out):
        super(DuelingDQN, self).__init__()
        self.n_actions = n_out

        self.fc1 = nn.Linear(n_in, n_hidden)
        self.fc2 = nn.Linear(n_hidden, 2*n_hidden)

        self.fc1_adv = nn.Linear(2*n_hidden, n_hidden)
        self.fc1_val = nn.Linear(2*n_hidden, n_hidden)

        self.fc2_adv = nn.Linear(n_hidden, self.n_actions)
        self.fc2_val = nn.Linear(n_hidden, 1)

    def forward(self, x):
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))

        adv = F.relu(self.fc1_adv(x))
        val = F.relu(self.fc1_val(x))

        adv = self.fc2_adv(adv)
        val = self.fc2_val(val).expand(x.size(0), self.n_actions)

        x = val + adv - adv.mean(1).unsqueeze(1).expand(x.size(0), self.n_actions)
        return x

pre_process = transforms.Compose([
    transforms.ToTensor(),
    transforms.Grayscale(),
    transforms.Resize([84,84]),
    #transforms.Normalize(0,255)
    ] )

class Agent(object):
    def __init__(self, args, render=False):
        self.env = gym.make(args.env)
        # self.env = gym.wrappers.Monitor(self.env, directory='monitors/'+args.env, force=True)
        n_in = self.env.observation_space.shape[0]
        ##TODO change code location and correct it back to the shape after preprocessing
        if len(self.env.observation_space.shape)==3: # observation is an image
            in_weight=self.env.observation_space.shape[0]
            in_height=self.env.observation_space.shape[1]
            in_channel = self.env.observation_space.shape[2]
            n_in = in_weight*in_height*in_channel

        n_out = self.env.action_space.n
        self.batch_size = args.batch_size
        self.game_name = args.env
        self.record_video = args.record_video
        self.save_model_every_epoch = args.save_model_every_epoch

        # Check if the folder exist to save the model dict
        if not os.path.exists(f'saved_models/{self.game_name}'):
            os.makedirs(f'saved_models/{self.game_name}')

        # writer to write data to tensorboard
        self.writer = SummaryWriter()

        # type of function approximator to use
        if args.model_type == 'CNN_2c2f':        
            self.model = CNN_2c2f().to(device)
        elif args.model_type == 'linear':
            self.model = LinearQN(n_in, n_out).to(device)
        elif args.model_type == 'dqn':
            self.model = DQN(n_in, args.n_hidden, n_out).to(device)
        else:
            self.model = DuelingDQN(n_in, args.n_hidden, n_out).to(device)

        # should experience replay be used
        if args.exp_replay:
            self.exp_replay = True
            self.memory = ReplayMemory(args.buffer_size)
        else:
            # memory of size 1 is same as using only the immediate transitions
            # this is only to keep the overall api similar for all cases
            self.memory = ReplayMemory(1)
            assert self.batch_size == 1

        # policy type
        if args.eps_greedy:
            self.eps_greedy = True
            self.eps_start = args.eps_start
            self.eps_end = args.eps_end
            self.eps_decay = args.eps_decay
        else:
            self.eps_greedy = False

        if args.optimizer == 'rmsprop':
            self.optimizer = optim.RMSprop(self.model.parameters())
        else:
            self.optimizer = optim.Adam(self.model.parameters(), lr=args.lr)

        self.gamma = args.gamma
        self.num_episodes = args.num_episodes
        self.loss_fn = args.loss_fn
        self.steps_done = 0
        self.episode_durations = []
        self.avg_rewards = []
        self.memory_burn_limit = args.memory_burn_limit

        if args.load_pretrained_model:
            # load the previous trained agent
            checkpoint = torch.load(args.model_path)
            self.model.load_stae_dict(checkpoint(['model_state_dict']))
            self.optimizer.load_state_dict(checkpoint(['optimizer_state_dict']))
        
        

    def select_action(self, state, train):
        if train:
            self.steps_done += 1
        # action will be selected based on the policy type : greedy or epsilon-greedy
        if self.eps_greedy:
            # smoothly decaying the epsilon threshold value as we progress
            if train:
                # eps_threshold = self.eps_end + (self.eps_start - self.eps_end) * math.exp(-1.*(self.steps_done/self.eps_decay))
                eps_threshold = (self.steps_done)*((self.eps_end - self.eps_start)/(self.eps_decay)) + self.eps_start
            else:
                eps_threshold = 0.05
            # explore or exploit?
            if random.random() > eps_threshold:
                with torch.no_grad():
                    action = self.model(state[None,:])
                return action.data.max(1)[1].view(1,1)
            else:
                return torch.tensor([[random.randrange(self.env.action_space.n)]],device = device, dtype=torch.long)
        else:
            with torch.no_grad():
               action = action = self.model(state[None,:])
            return action.data.max(1)[1].view(1,1)

    # Here we'll deal with the empty memory problem: we pre-populate our memory by taking random actions 
    # and storing the experience (state, action, reward, next_state).
    def burn_memory(self):

        steps = 0
        state = torch.zeros(4,84,84, device = device)
        next_state = torch.zeros(4,84,84, device =device)

        state_single = self.env.reset()
        #state_single = rgb2gray(resize(state_single,(84,84)))
        state_single = pre_process(state_single)

        state[0,:,:] = state_single
        state[1,:,:] = state_single
        state[2,:,:] = state_single
        state[3,:,:] = state_single
    

        print('Starting to fill the memory with random policy')
        while steps < self.memory_burn_limit:
            #Executing a random policy
            action = torch.tensor([[random.randrange(self.env.action_space.n)]], device=device, dtype=torch.long)
            next_state_single, reward, is_terminal, _ = self.env.step(action)   
            reward = torch.tensor([reward], device = device)             
            #next_state_single = rgb2gray(resize(next_state_single,(84,84))) 
            next_state_single = pre_process(next_state_single)

            next_state[0,:,:] = state[1,:,:]
            next_state[1,:,:] = state[2,:,:]
            next_state[2,:,:] = state[3,:,:]
            next_state[3,:,:] = next_state_single

            # Store the transition in memory
            self.memory.push(state, action, next_state, reward)

            # Move to next step
            steps += 1
            state = next_state

            #If the next_state is terminal, then you reset it
            if is_terminal:
                state_single = self.env.reset()
                #state_single = rgb2gray(resize(state_single,(84,84)))
                state_single = pre_process(state_single)

                state[0,:,:] = state_single
                state[1,:,:] = state_single
                state[2,:,:] = state_single
                state[3,:,:] = state_single
                    
        print('Memory filled, ready to start training now')
        print("-"*50)

################################################################################################################################################
    def testing_random_play(self):

        state = self.env.reset()
        #state is 210,160,3            

        for i in range(1000):
            action = random.randrange(self.env.action_space.n)
            next_state, reward, is_terminal, _ = self.env.step(action)
            print(reward,is_terminal)
            self.env.render()
        print('Random play done now')
        
################################################################################################################################################

    def play_episode(self, e, train=True):

        state_single = self.env.reset()
        size = (state_single.shape[0],state_single.shape[1])
        if self.record_video >0 and e%self.record_video == 0:
            out = cv2.VideoWriter(f'played_out/{self.game_name}/project_{e}.avi',cv2.VideoWriter_fourcc(*'DIVX'), 15, size)

        state_single = pre_process(state_single)
            
        state = torch.zeros(4,84,84,device = device)
        next_state = torch.zeros(4,84,84,device = device)

        state[0,:,:] = state_single
        state[1,:,:] = state_single
        state[2,:,:] = state_single
        state[3,:,:] = state_single

        steps = 0
        total_reward = 0
        # iterate till the terminal state is reached
        while True:
            if self.record_video >0 and e%self.record_video == 0:
                out.write(self.env.render('rgb_array'))
                
            #self.env.render(mode='rgb_array')
            action = self.select_action(state,train)
            # print("action: ", action)
            next_state_single, reward, is_terminal, _ = self.env.step(action)
            reward = torch.tensor([reward], device = device)
            #next_state_single = rgb2gray(resize(next_state_single,(84,84))) 
            next_state_single = pre_process(next_state_single)
            next_state[0,:,:] = state[1,:,:]
            next_state[1,:,:] = state[2,:,:]
            next_state[2,:,:] = state[3,:,:]
            next_state[3,:,:] = next_state_single

            total_reward += reward

            # store the transition in memory
            self.memory.push(state, action, next_state, reward)
            if is_terminal:
                self.writer.add_scalar('total_reward/train', total_reward, e)
                self.writer.add_scalar('episode_duration/train', steps, e)
                self.episode_durations.append(steps)
                print("Episode {} completed after {} steps | Total steps = {} | Total reward = {}".format(e,steps,self.steps_done, total_reward.item()))
                if self.record_video >0 and e%self.record_video == 0:
                    out.release()
                self.plot_durations()
                # self.plot_rewards()  
                return total_reward

            if train:
                # backprop and learn; otherwise just play the policy
                self.optimize_model()
                
            # update state
            state = next_state
            steps += 1  

    def optimize_model(self):
        # check if enough experience collected so far
        # the agent continues with a random policy without updates till then
        if len(self.memory) < self.batch_size:
            return 

        self.optimizer.zero_grad()
        # sample a random batch from the replay memory to learn from experience
        # for no experience replay the batch size is 1 and hence learning online
        transitions = self.memory.sample(self.batch_size)
        batch = Transition(*zip(*transitions))

        # Compute a mask of non-final states and concatenate the batch elements
        # (a final state would've been the one after which simulation ended)
        non_final_mask = torch.tensor(tuple(map(lambda s: s is not None,
                                            batch.next_state)), device=device, dtype=torch.bool)
        
        batch_next_state = torch.stack([s for s in batch.next_state if s is not None])
        batch_state = torch.stack(batch.state)
        batch_action = torch.stack(batch.action)
        batch_reward = torch.stack(batch.reward)

        # There is no separate target Q-network implemented and all updates are done
        # synchronously at intervals of 1 unlike in the original paper
        # current Q-values: gather(dim, index) return the elements along the dim axis with given index.
        current_Q = self.model(batch_state).gather(1, batch_action.view([-1,1]))
        # expected Q-values (target)
        max_next_Q = torch.zeros(self.batch_size, device=device)
        max_next_Q[non_final_mask] = self.model(batch_next_state).max(1)[0].detach()

        expected_Q = (self.gamma * max_next_Q.view([-1,1])).data + batch_reward

        # loss between current Q values and target Q values
        if self.loss_fn == 'l1':
            loss = F.smooth_l1_loss(current_Q, expected_Q)
        else:
            loss = F.mse_loss(current_Q, expected_Q)

        # backprop the loss
        loss.backward()
        self.optimizer.step()

        return batch_reward.sum() # return the average of reward of the training data for reference
        
        # TODO write average reward and loss after each batch training
        #self.writer.add_scalar('avg_loss/train', loss.mean(), e)

    def plot_durations(self):
        durations = torch.FloatTensor(self.episode_durations)
        plt.figure(1)
        plt.clf()
        plt.title('Training')
        plt.xlabel('Episode')
        plt.ylabel('Duration')
        plt.plot(durations.numpy())
        # Averaging over 100 episodes and plotting those values
        if len(durations) >= 100:
            means = durations.unfold(0, 100, 1).mean(1).view(-1)
            means = torch.cat((torch.zeros(99), means))
            plt.plot(means.numpy())
        # pause so that the plots are updated
        plt.pause(0.001)

    def plot_rewards(self):
        plt.figure(2)
        plt.clf()
        plt.title('Training')
        plt.ylabel('Avg Reward')
        plt.plot(self.avg_rewards)
        # pause so that the plots are updated
        plt.pause(0.001)
        # plt.show()

    def train(self):
        print("Going to be training for a total of {} episodes".format(self.num_episodes))
        for e in range(self.num_episodes):
            # print("----------- Episode {} -----------".format(e))
            total_reward = self.play_episode(e,train=True) 
            if self.save_model_every_epoch > 0 and e%self.save_model_every_epoch == 0:
                # Save model to /saved_models/game_name/model_trained_epoch.pt
                torch.save({
                    'epoch': e,
                    'model_state_dict': self.model.state_dict(),
                    'optimizer_state_dict': self.optimizer.state_dict(),
                    'reward': total_reward,
                    }, f'saved_models/{self.game_name}/model_trained_{e}.pt')


    def test(self,num_episodes):
        total_reward = 0
        print("-"*50)
        print("Testing for {} episodes".format(num_episodes))
        for e in range(num_episodes):
            total_reward += self.play_episode(e,train=False)
        print("Running policy after training for {} updates".format(self.steps_done))
        print("Avg reward achieved in {} episodes : {}".format(num_episodes, total_reward/num_episodes))
        print("-"*50)
        self.avg_rewards.append(total_reward/num_episodes)
        # self.plot_rewards()

    def close(self):
        #self.env.render(close=True)
        self.env.close()
        plt.ioff()
        plt.show()

def parse_arguments():
    parser = argparse.ArgumentParser(description='Deep Q Network Argument Parser')
    parser.add_argument('--env',type=str, default='SpaceInvaders-v0')
    parser.add_argument('--render',type=int,default=0)
    parser.add_argument('--model_type',type=str, default='CNN_2c2f',help ='Model type one of (linear,dqn,duel)')
    parser.add_argument('--exp_replay', type=int, default=1, help='should experience replay be used, default 1')
    parser.add_argument('--num_episodes', type=int, default=10, help='number of episodes')
    parser.add_argument('--batch_size', type=int, default=2, help='batch size')
    parser.add_argument('--buffer_size', type=int, default=500, help='Replay memory buffer size')
    # parser.add_argument('--n_in', type=int, default=4, help='input layer size')
    # parser.add_argument('--n_out', type=int, default=256, help='output layer size')
    parser.add_argument('--loss_fn', type=str, default='l2', help='loss function one of (l1,l2) | Default: l1')
    parser.add_argument('--optimizer', type=str, default='rmsprop', help='optimizer one of (rmsprop,adam) | Default : rmsprop')
    parser.add_argument('--n_hidden', type=int, default=32, help='hidden layer size')
    parser.add_argument('--gamma', type=float, default=0.99, help='discount factor')
    parser.add_argument('--lr', type=float, default=0.0001, help='learning rate')
    parser.add_argument('--frame_hist_len', type=int, default=4, help='frame history length | Default : 4')
    parser.add_argument('--eps_greedy', type=int, default=1, help='should policy be epsilon-greedy, default 1')
    parser.add_argument('--eps_start', type=float, default=0.95, help='e-greedy threshold start value')
    parser.add_argument('--eps_end', type=float, default=0.05, help='e-greedy threshold end value')
    parser.add_argument('--eps_decay', type=int, default=100000, help='e-greedy threshold decay')
    parser.add_argument('--logs', type=str, default = 'logs',  help='logs path')
    parser.add_argument('--memory_burn_limit', type=int,default=200, help='Till when to burn memory')
    parser.add_argument('--record_video',type=int, default=10, help='Make record video every # episode')
    parser.add_argument('--load_pretrained_model', type=int, default=0, help='load pretrained mode')
    parser.add_argument('--save_model_every_epoch', type=int, default=10, help='Save model every amount of epochs')
    parser.add_argument('--model_path',type=str,default="model_saved.pt", help='File path to the pretrained model')
    return parser.parse_args()

def main():
    plt.ion()
    # plt.figure()
    # plt.show()

    args = parse_arguments()
    print(args)
    agent  = Agent(args)

    #agent.testing_random_play()    
    #pdb.set_trace()

    agent.burn_memory()
    #pdb.set_trace()
    agent.train()
    print('----------- Completed Training -----------')
    agent.test(num_episodes=10)
    print('----------- Completed Testing -----------')

    agent.close()

    ### Visualize the training progress by:
    #  pip install tensorboard
    #  tensorboard --logdir=runs

if __name__ == '__main__':
    main()

#TODO
#2) Storing none for next_state if it's terminal state during burning
#3) Check is this required in burning memory.......            #while steps == self.memory_burn_limit and not is_terminal:
#4) Take a look at the resized images and crop the center region
 