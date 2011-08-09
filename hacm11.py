import os, sys, math
from twisted.internet.serialport import SerialPort
from twisted.protocols import basic
import ConfigParser
from plugins.pluginapi import PluginAPI
from twisted.internet import reactor
from twisted.python import log
import xml.etree.ElementTree as ET

# Platform specific imports
if os.name == "nt":
    import win32service
    import win32serviceutil
    import win32event
    import win32evtlogutil

CMD_ALLUNITSOFF =   0x00
CMD_ALLLIGHTSON =   0x01
CMD_ON =            0x02
CMD_OFF =           0x03
CMD_DIM =           0x04
CMD_BRIGHT =        0x05
CMD_ALLLIGHTSOFF =  0x06
CMD_EXTENDEDCODE =  0x07
CMD_HAILREQUEST =   0x08
CMD_PRESETDIM1 =    0x09
CMD_PRESETDIM2 =    0x0A
CMD_STATUSON =      0x0B
CMD_STATUSOFF =     0x0C
CMD_STATUSREQUEST = 0x0D

class Device:
    def __init__(self, hcdc):
        self.hcdc = hcdc
        self.standarddim = False
        self.presetdim = False
        self.allunitsoff = False
        self.alllightsoff = False
        self.alllightson = False
        self.statusrequest = False
        self.reportstatus = False
        self.status = 'Unknown'
        self.value = 0
        
listDevices = []

        
class CM11Protocol(basic.LineReceiver):
    '''
    This class handles the CM11a protocol, i.e. the wire level stuff.
    '''
    def __init__(self, wrapper):
        self.wrapper = wrapper
        self.readingdata = False
        self.recbits = 0
        self.recbytes = 0
        self.recbuf = []
        self.sndbuf = []
        self.updbuf = []
        self.address = {}
        self.hex = ''
        self.message = False
        self.bReady = True
        self.call = None
        self.msglen = 0
        self.failedSendAttempts = 0
        self.waitonchecksum = False
        self.checksum = 0
        self.dc = ['13', '5', '3', '11', '15', '7', '1', '9', '14', '6', '4', '12', '16', '8', '2', '10']
        self.hc = ['M', 'E', 'C', 'K', 'O', 'G', 'A', 'I', 'N', 'F', 'D', 'L', 'P', 'H', 'B', 'J']
        self.cmd = ['All units off',
                     'All lights on',
                     'On',
                     'Off',
                     'Dim',
                     'Bright',
                     'All lights off',
                     'Extended code',
                     'Hail request',
                     'Hail acknowledge',
                     'Preset Dim 1',
                     'Preset Dim 2',
                     'Extended data transfer',
                     'Status on',
                     'Status off',
                     'Status request']
        self.extdim = [0, 2, 3, 5, 6, 8, 10, 11, 13, 15, 
                       16, 18, 19, 21, 23, 24, 26, 27, 29, 31,
                       32, 34, 35, 37, 39, 40, 42, 44, 45, 47,
                       48, 50, 52, 53, 55, 56, 58, 60, 61, 63,
                       65, 66, 68, 69, 71, 73, 74, 76, 77, 79,
                       81, 82, 84, 85, 87, 89, 90, 92, 94, 95,
                       97, 98, 100, 100] 
   
    def connectionMade(self):
        log.msg("Connected to CM11 interface...")
        
    def connectionLost(self, reason):
        log.msg("Lost connection") 

    def dataReceived(self, data):        
        """
        Event fired when data has been received.
        """
        for char in data:
            
            # If we were waiting on a checksum, this must be the checksum
            if self.waitonchecksum == True:
                self.waitonchecksum = False
                log.msg("Received checksum: %02x" % ord(char))
                if ord(char) == self.checksum:
                    log.msg("Correct checksum")
                    self.failedSendAttempts = 0
                    self.transport.write('\x00')
                else:
                    log.msg("Checksum should have been: %02x" % self.checksum)
                    self.failedSendAttempts += 1
                    log.msg("Failed send attempts: %d" % self.failedSendAttempts)
                    if self.failedSendAttempts < 3:
                        # Resend
                        self.sendcommand()
                    else:
                        self.failedSendAttempts = 0
                        log.msg("ERROR: Maximum number of failed send attempts reached, delete command from queue")
                        self.waitonchecksum = False
                        # Remove command from send queue
                        del self.sndbuf[0]
                        if len(self.sndbuf):
                            self.sendcommand()
                        

            # The maximum number of bytes received as a data block should be less
            # than 10 bytes. The first byte received that is part of a data block
            # indicates the number of bytes to follow. This means that whenever 
            # this first byte is less than 10 it is probably the start of a data block.
            elif ord(char) < 10 or self.readingdata == True:
                self.readbuffer(ord(char)) 

            # 0xa5 is a request for a clockupdate after a power failure, respond accordingly.
            elif ord(char) == 0xa5:
                log.msg("The CM11 sends a clock update poll (recovering from a power failure)")
                self.transport.write('\x9b\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')
               
            # 0x5a is a poll request to indicate new data is ready to be read
            elif ord(char) == 0x5a:
                log.msg("The CM11 sends a poll request")
                # Send 0xc3 to start reading the data buffer
                self.transport.write('\xc3')
              
            # 0x55 is a poll to indicate the CM11 is ready to receive commands
            elif ord(char) == 0x55:
                log.msg("Command has been send")
                self.bReady = True
                del self.sndbuf[0]
                if len(self.sndbuf):
                    self.sendcommand()
                 
            else:  
                log.msg("Invalid or unsupported CM11 message received: %d" % ord(char))

    def readbuffer(self, byte):
        # If this is the first byte of a new message do some special things
        if self.msglen == 0:
            self.readingdata = True
            # Determine the number of bytes to follow
            self.msglen = byte 
            # Initialize the message buffer
            self.recbuf = []
            # Start timer for the period (2 sec) in which a complete message should be received,
            # but first cancel any running timer
            try:
                self.call.cancel()
            except:
                pass
            self.call = self.wrapper.reactor.callLater(2, self.timedout)
            
        else:
            # Append the byte to the message buffer 
            self.recbuf.append(byte)
            # Update the number of bytes to follow
            self.msglen -= 1
            # If no bytes should follow 
            if self.msglen == 0:
                self.readingdata = False
                self.processmsg(self.recbuf)

    def processmsg(self, buffer):
        mask = 1        
        index = 0
        
        # Cancel timer
        try:
            self.call.cancel()
        except:
            pass
          
        # Process the received bytes
        while index < len(buffer) - 1:
            #log.msg("Index: %d, Length: %d, Mask: %02x, Char: %02X" % (index, len(buffer), mask, buffer[index+1]))
            
            # Process commands (apply the function/address mask to the first byte in the buffer
            # to determine whether the next byte to be processed is an address or a function
            if buffer[0] & mask:
                
                # Received a BRIGHT or DIM command
                if (buffer[index + 1] & 0x0F) == CMD_DIM or (buffer[index + 1] & 0x0F) == CMD_BRIGHT:
                    if len(buffer) > 2:
                        # Issue for each specified device with the specified house code the specified command
                        try:
                            for dc in self.address[self.hc[(buffer[index + 1] & 0xF0) >> 4]]:
                                log.msg("%s%s %s %d (%d%%)" % (self.hc[(buffer[index + 1] & 0xF0) >> 4], dc, self.cmd[buffer[index + 1] & 0x0F],  buffer[index + 2], buffer[index + 2] * 100 / 210))
                                
                                hcdc = self.hc[(buffer[index + 1] & 0xF0) >> 4] + dc
                                item = next((i for i in xrange(len(listDevices)) if listDevices[i].hcdc == hcdc), None)
                                if item is not None:
                                    if listDevices[item].standarddim == True or listDevices[item].presetdim == True:
                                        listDevices[item].status = "Dimmed"
                                        
                                        if (buffer[index + 1] & 0x0F) == CMD_DIM:
                                            listDevices[item].value -= (buffer[index + 2] * 100 / 210)
                                            if listDevices[item].value < 0:
                                                listDevices[item].value = 0
                                                listDevices[item].status = "Off"

                                        if (buffer[index + 1] & 0x0F) == CMD_BRIGHT:
                                            listDevices[item].value += (buffer[index + 2] * 100 / 210)
                                            if listDevices[item].value > 100:
                                                listDevices[item].value = 100
                                                listDevices[item].status = "On"
                                        
                                        attribs = {"Status": listDevices[item].status, "Level": listDevices[item].value}
                                
                                        # Send update to core
                                        self.wrapper.pluginapi.value_update(hcdc, attribs)

                                    else:
                                        log.msg("The addressed device (%s) is not dimmable" % hcdc)
                            
                                else:
                                    log.msg("Device '%s' is not monitored" % hcdc)

                            # Clear list of specified devices                
                            self.address = {}
                      
                        except:
                            # When only a house code has been specified and no device code, there is nothing to do
                            log.msg("Error received a DIM or BRIGHT command without a device has been specified")

                        # Increase the index of bytes to be processed
                        index += 2
                        mask <<= 2

                    else:
                        log.msg("Error: A DIM or BRIGHT command needs more bytes")
                        break
                    
                # Received an EXTENDED CODE
                elif (buffer[index + 1] & 0x0F) == CMD_EXTENDEDCODE:
                    
                    # When a Preset Dim command is received
                    if buffer[index + 4] == 0x31:
                        log.msg("%s%s Extended Preset Dim %d%%" % (self.hc[(buffer[index + 2] & 0xF0) >> 4], self.dc[buffer[index + 2] & 0x0F], self.extdim[buffer[index + 3]]))
                            
                        hcdc = self.hc[(buffer[index + 1] & 0xF0) >> 4] + self.dc[buffer[index + 2] & 0x0F]

                        # First check if the addressed device is dimmable
                        item = next((i for i in xrange(len(listDevices)) if listDevices[i].hcdc == hcdc), None)
                        if item is not None:
                            if listDevices[item].standarddim == True or listDevices[item].presetdim == True:
                                if self.extdim[buffer[index + 3]] == 0:
                                    listDevices[item].status = "Off"
                                    listDevices[item].value = 0
                                elif self.extdim[buffer[index + 3]] == 100:
                                    listDevices[item].status = "On"
                                    listDevices[item].value = 100
                                else:
                                    listDevices[item].status = "Dimmed"
                                    listDevices[item].value = self.extdim[buffer[index + 3]]

                                attribs = {"Status": listDevices[item].status, "Level": self.extdim[buffer[index + 3]]}
                                
                                # Send update to core
                                self.wrapper.pluginapi.value_update(hcdc, attribs)

                            else:
                                log.msg("The addressed device (%s) is not dimmable" % hcdc)
                            
                        else:
                            log.msg("Device '%s' is not monitored" % hcdc)
                            
                        # Increase the index of bytes to be processed
                        index += 4
                        mask <<= 4

                    else:
                        if len(buffer) > 4:
                            log.msg("Error: An EXTENDED COMMAND needs more bytes")
                            break

                # When a STATUS ON or STATUS OFF command has been received, log it but ignore it
                elif (buffer[index + 1] & 0x0F) == CMD_STATUSON or (buffer[index + 1] & 0x0F) == CMD_STATUSOFF:
                    log.msg("%s%s %s" % (self.hc[(buffer[index + 1] & 0xF0) >> 4], self.address[self.hc[(buffer[index + 1] & 0xF0) >> 4]][0], self.cmd[buffer[index + 1] & 0x0F]))

                    # Increase the index of bytes to be processed
                    index += 1
                    mask <<= 1
                
                # When an ALL LIGHTS OFF command has been received
                elif (buffer[index + 1] & 0x0F) == CMD_ALLLIGHTSOFF:
                    log.msg("%s %s" % (self.hc[(buffer[index + 1] & 0xF0) >> 4], self.cmd[buffer[index + 1] & 0x0F]))

                    # Find all devices with this house code which respond to All Lights Off
                    for device in listDevices:
                        if device.hcdc.lower()[0] == self.hc[(buffer[index + 1] & 0xF0) >> 4].lower():
                            if device.alllightsoff == True:
                                device.status = "Off"
                                if device.standarddim == True or device.presetdim == True:
                                    device.value = 0
                                    attribs = {"Status": device.status, "Level": device.value}
                                else:
                                    attribs = {"Status": device.status}
                                    
                                # Send update to core
                                self.wrapper.pluginapi.value_update(device.hcdc, attribs)

                    # Increase the index of bytes to be processed
                    index += 1
                    mask <<= 1

                # When an ALL LIGHTS ON command has been received
                elif (buffer[index + 1] & 0x0F) == CMD_ALLLIGHTSON:
                    log.msg("%s %s" % (self.hc[(buffer[index + 1] & 0xF0) >> 4], self.cmd[buffer[index + 1] & 0x0F]))

                    # Find all devices with this house code which respond to All Lights On
                    for device in listDevices:
                        if device.hcdc.lower()[0] == self.hc[(buffer[index + 1] & 0xF0) >> 4].lower():
                            if device.alllightson == True:
                                device.status = "On"
                                if device.standarddim == True or device.presetdim == True:
                                    device.value = 100
                                    attribs = {"Status": device.status, "Level": device.value}
                                    
                                else:
                                    attribs = {"Status": device.status}
                                    
                                # Send update to core
                                self.wrapper.pluginapi.value_update(device.hcdc, attribs)

                    # Increase the index of bytes to be processed
                    index += 1
                    mask <<= 1

                # When an ALL UNITS OFF command has been received
                elif (buffer[index + 1] & 0x0F) == CMD_ALLUNITSOFF:
                    log.msg("%s %s" % (self.hc[(buffer[index + 1] & 0xF0) >> 4], self.cmd[buffer[index + 1] & 0x0F]))

                    # Find all devices with this house code which respond to All Units On
                    for device in listDevices:
                        if device.hcdc.lower()[0] == self.hc[(buffer[index + 1] & 0xF0) >> 4].lower():
                            if device.allunitsoff == True:
                                device.status = 'Off'
                                if device.standarddim == True or device.presetdim == True:
                                    device.value = 0
                                    attribs = {"Status": device.status, "Level": device.value}
                                else:
                                    attribs = {"Status": device.status}

                                # Send update to core
                                self.wrapper.pluginapi.value_update(device.hcdc, attribs)

                    # Increase the index of bytes to be processed
                    index += 1
                    mask <<= 1

                # When an ON or OFF command has been received
                elif (buffer[index + 1] & 0x0F) == CMD_ON or (buffer[index + 1] & 0x0F) == CMD_OFF:
                    try:
                        for dc in self.address[self.hc[(buffer[index + 1] & 0xF0) >> 4]]:
                            log.msg("%s%s %s" % (self.hc[(buffer[index + 1] & 0xF0) >> 4], dc, self.cmd[buffer[index + 1] & 0x0F]))
                            
                            hcdc = self.hc[(buffer[index + 1] & 0xF0) >> 4] + dc
                            device = next((device for device in listDevices if device.hcdc == hcdc), None)
                            if device is not None:
                                device.status = self.cmd[buffer[index + 1] & 0x0F]
                                if device.standarddim == True or device.presetdim == True:
                                    if (buffer[index + 1] & 0x0F) == CMD_ON:
                                        device.value = 100
                                    else:
                                        device.value = 0
                                        
                                    attribs = {"Status": device.status, "Level": device.value}
                                else:
                                    attribs = {"Status": device.status}

                                self.wrapper.pluginapi.value_update(hcdc, attribs)
                                    
                            else:
                                log.msg("Device '%s' is not monitored" % hcdc)
                                                          
                        # Clear the list of specified devices
                        self.address = {}
                        
                    except KeyError:
                        log.msg("%s %s" % (self.hc[(buffer[index + 1] & 0xF0) >> 4], self.cmd[buffer[index + 1] & 0x0F]))
                        log.msg("Error: Received an ON or OFF command without a device has been specified")

                    # Increase the index of bytes to be processed
                    index += 1
                    mask <<= 1

                # Log but ignore all other X10 command                        
                else:
                    # All other commands
                    # Issue for each specified device with the specified house code the specified command
                    try:
                        for dc in self.address[self.hc[(buffer[index + 1] & 0xF0) >> 4]]:
                            log.msg("%s%s %s" % (self.hc[(buffer[index + 1] & 0xF0) >> 4], dc, self.cmd[buffer[index + 1] & 0x0F]))
                            
                        # Clear the list of specified devices
                        self.address = {}
                        
                    except KeyError:
                        log.msg("%s %s" % (self.hc[(buffer[index + 1] & 0xF0) >> 4], self.cmd[buffer[index + 1] & 0x0F]))
                            
                    # Increase the index of bytes to be processed
                    index += 1
                    mask <<= 1

            # This must be a device code            
            else:
                # Add specified device to the list of specified devices
                try:
                    # Add this device to the array holding all specified device codes within the same house code
                    self.address[self.hc[(buffer[index + 1] & 0xF0) >> 4]].append(self.dc[buffer[index + 1] & 0x0F])
                except KeyError:
                    # First create the array for the house code when this is the first device part of this house code
                    self.address[self.hc[(buffer[index + 1] & 0xF0) >> 4]] = []
                    self.address[self.hc[(buffer[index + 1] & 0xF0) >> 4]].append(self.dc[buffer[index + 1] & 0x0F])
                    
                # Increase the index of bytes to be processed
                index += 1
                mask <<= 1    
    
    def timedout(self):
        log.msg("Timeout reading the CM11 buffer")
        self.readingdata = False

    def poweron(self, hcdc):
        """
        Set status of specified device to on
        """
        # Update in memory list and send update to core
        item = next((i for i in xrange(len(listDevices)) if listDevices[i].hcdc == hcdc), None)
        if item is not None:
            listDevices[item].Status = "On"
            if listDevices[item].standarddim == True or listDevices[item].presetdim == True:
                self.wrapper.pluginapi.value_update(hcdc, {"Status": "On", "Level": 100})
                listDevices[item].Value = 100
            else:
                self.wrapper.pluginapi.value_update(hcdc, {"Status": "On"})
        else:
            log.msg("Error: '%s' is not in in-memory list")

        # Put the device address on the send queue
        self.sndbuf.append(chr(0x04) + chr(self.hc.index(hcdc[0].upper()) << 4 | self.dc.index(hcdc[1:])))
        # Put the on command on the send queue 
        self.sndbuf.append(chr(0x06) + chr(self.hc.index(hcdc[0].upper()) << 4 | self.cmd.index('On')))
        self.sendcommand()
        
    def poweroff(self, hcdc):
        """
        Set status of specified module to off
        """
        # Update in memory list and send update to core
        item = next((i for i in xrange(len(listDevices)) if listDevices[i].hcdc == hcdc), None)
        if item is not None:
            listDevices[item].Status = "Off"
            if listDevices[item].standarddim == True or listDevices[item].presetdim == True:
                self.wrapper.pluginapi.value_update(hcdc, {"Status": "Off", "Level": 0})
                listDevices[item].Value = 0
            else:
                self.updbuf.append({"address": hcdc, "message": {"Status": "Off"}})
        else:
            log.msg("Error: '%s' is not in in-memory list")

        # Put the device address on the send queue
        self.sndbuf.append('\04' + chr(self.hc.index(hcdc[0].upper()) << 4 | self.dc.index(hcdc[1:])))
        # Put the on command on the send queue 
        self.sndbuf.append('\06' + chr(self.hc.index(hcdc[0].upper()) << 4 | self.cmd.index('Off')))
        self.sendcommand()

    def dim(self, hcdc, level):
        """
        Dim the specified device to the specified level
        """
        # Update in memory list and send update to core
        item = next((i for i in xrange(len(listDevices)) if listDevices[i].hcdc == hcdc), None)
        if item is None:
            log.msg("Error: '%s' is not in in-memory list")
        else:
            if listDevices[item].standarddim == True:
                log.msg("DEBUG: Level: %s, Current: %d" % (level, listDevices[item].value))
                if listDevices[item].value > int(level):
                    # Calculate number of dim actions
                    dims = int((listDevices[item].value - int(level)) * 22 / 100)
                    newlevel = listDevices[item].value - dims * 100 / 22
                    log.msg("DIMS: %d (%f)" % (dims, dims * 100 / 22))
                                   
                    # Put the house and device code on the send queue
                    self.sndbuf.append('\04' + chr(self.hc.index(hcdc[0].upper()) << 4 | self.dc.index(hcdc[1:])))
                    
                    # Put the command on the send queue
                    self.sndbuf.append(chr(dims << 3 | 0x06) +
                                       chr(self.hc.index(hcdc[0].upper()) << 4 | CMD_DIM) 
                                       )
                    log.msg("DIM: %02X %02X" % (dims << 3 | 0x06, self.hc.index(hcdc[0].upper()) << 4 | CMD_DIM))
                    log.msg("DIM to %d%%" % newlevel)
                
                else:
                    # Calculate number of bright actions
                    brights = int((int(level) - listDevices[item].value) * 22 / 100)
                    newlevel = listDevices[item].value + brights * 100 / 22
                    log.msg("BRIGHTS: %d (%d)" % (brights, brights * 100/22))
                                   
                    # Put the house and device code on the send queue
                    self.sndbuf.append('\04' + chr(self.hc.index(hcdc[0].upper()) << 4 | self.dc.index(hcdc[1:])))
                    
                    # Put the command on the send queue
                    self.sndbuf.append(chr(brights << 3 | 0x06) +
                                       chr(self.hc.index(hcdc[0].upper()) << 4 | CMD_BRIGHT) 
                                       )

                    log.msg("BRIGHT: %02X %02X" % (brights << 3 | 0x06, self.hc.index(hcdc[0].upper()) << 4 | CMD_BRIGHT))
                    log.msg("BRIGHT to %d%%" % newlevel)

            elif listDevices[item].presetdim == True:
                log.msg("Send extended preset dim command")
                
                # Determine the data to be send (find exact or next higher match for the specified level)
                for i in range (0,63):
                    if self.extdim[i] >= int(level) and i > 0:
                        newlevel = self.extdim[i]
                        break
                    
                # Put the header, code, data and command on the send queue
                self.sndbuf.append(chr(0x07) + 
                                   chr(self.hc.index(hcdc[0].upper()) << 4 | 0x07) +
                                   chr(self.dc.index(hcdc[1:])) +
                                   chr(i) +
                                   chr(0x31)    
                                   )

            else:
                log.msg("The specified device '%' does not support dimming" % hcdc)
                return    

            # Update in memory list and send update to core
            if newlevel == 0:
                listDevices[item].Status = "Off"
            elif newlevel == 100:
                listDevices[item].Status = "On"
            else:
                listDevices[item].Status = "Dimmed"
            
            listDevices[item].value = newlevel
                    
            # Send update to the core
            self.wrapper.pluginapi.value_update(hcdc, {"Status": listDevices[item].Status, "Level": newlevel})
                
            # Send the command
            self.sendcommand()


    def sendcommand(self):
        """
        Send a command to the CM11
        """
        if len(self.sndbuf) == 0:
            log.msg("No commands left in the send queue")
        else:
            # Calculate checksum
            self.checksum = 0
            for byte in self.sndbuf[0]:
                self.checksum += ord(byte)
                log.msg("SEND ON POWERLINE: %02x" % ord(byte))
            self.checksum &= 0xff
            if self.bReady == True:
                self.waitonchecksum = True
                self.bReady = False
                self.transport.write(self.sndbuf[0])
            else:
                log.msg("Waiting to send")

    
class CM11Wrapper():
    def __init__(self):
        '''
        Load initial CM11 configuration from hacm11.conf
        '''
        from utils.generic import get_configurationpath
        self.config_path = get_configurationpath()
        
        config = ConfigParser.RawConfigParser()
        config.read(os.path.join(self.config_path, 'hacm11.conf'))
        

        print "DEBUG: get_configurationpath: ", self.config_path
        print "DEBUG: os.getcwd: ", os.getcwd( )
        print "DEBUG: sys.path:", sys.path
        
        # Get serial port information
        self.port = config.get("serial", "port")

        # Get broker information (RabbitMQ)
        self.broker_host = config.get("broker", "host")
        self.broker_port = config.getint("broker", "port")
        self.broker_user = config.get("broker", "username")
        self.broker_pass = config.get("broker", "password")
        self.broker_vhost = config.get("broker", "vhost")
        
        self.logging = config.getboolean('general', 'logging')
        self.id = config.get('general', 'id')

        self.pluginapi = PluginAPI(plugin_id=self.id, plugin_type='x10', logging=self.logging,
                                   broker_ip=self.broker_host, broker_port=self.broker_port,
                                   username=self.broker_user, password=self.broker_pass, vhost=self.broker_vhost)
        
        self.pluginapi.register_poweron(self)
        self.pluginapi.register_poweroff(self)
        self.pluginapi.register_dim(self)
        self.pluginapi.register_custom(self)
        
        self.protocol = CM11Protocol(self)
        self.reactor = None 
  
        # Initialize in-memory device list from xml file
        xml_file = os.path.join(self.config_path, 'x10.xml')
        try:
            xmldoc = ET.parse(xml_file)
            devices = xmldoc.findall('device')
            for device in devices:
                newdevice = Device(device.get('hcdc'))
                newdevice.standarddim = (device.findtext('standarddim', 'False').lower() == "true")
                newdevice.presetdim = (device.findtext('presetdim', 'False').lower() == "true")
                newdevice.allunitsoff = (device.findtext('allunitsoff', 'False').lower() == "true")
                newdevice.alllightsoff = (device.findtext('alllightsoff', 'False').lower() == "true")
                newdevice.alllightson = (device.findtext('alllightson', 'False').lower() == "true")
                newdevice.statusrequest = (device.findtext('statusrequest','False').lower() == "true")
                newdevice.reportstatus = (device.findtext('reportstatus','False').lower() == "true")
                listDevices.append(newdevice)

        except:
            log.msg("Error opening or reading '%s' (%s)" % (xml_file, sys.exc_info()[1]))
        
        #device = next((device for device in listDevices if device.hcdc == 'M5'), None)
        #index = next((i for i in xrange(len(listDevices)) if listDevices[i].hcdc == 'M5'), None)
        #if index is not None:
        #    print listDevices[index].hcdc
        #    device = listDevices.pop(index)
        #    print "HCDC: ", device.hcdc
        #    print "allunitsoff: ", device.allunitsoff
        
    def start(self):
        '''
        Function that starts the CM11 plug-in. It handles the creation 
        of the plugin connection and connects to the specified serial port.
        '''
        
        #FIXME: Error when serial port does not exist or cannot be opened
        try:
            myserial = SerialPort (self.protocol, self.port, reactor)
            myserial.setBaudRate(4800)      
            self.reactor = reactor
        
            reactor.run(installSignalHandlers=0)
            return(True)
        except:
            log.msg("Error opening serial port %s (%s)" % (self.port, sys.exc_info()[1]))
            return(False)

    def on_poweron(self, hcdc):
        log.msg("Turn %s on" % hcdc)
        self.protocol.poweron(hcdc)
        return {'processed': True} 

    def on_poweroff(self, hcdc):
        log.msg("Turn %s off" % hcdc)
        self.protocol.poweroff(hcdc)
        return {'processed': True}

    def on_dim(self, hcdc, level):
        log.msg("Dim %s to %s%%" % (hcdc, level))
        self.protocol.dim(hcdc, level)
        return {'processed': True} 
    
    def on_custom(self, command, parameters):
        """
        Handles several custom actions used througout the plugin.
        """
        log.msg("on_custom event received")
        
        if command == 'add_characteristics':
            '''
            This command sets the characteristics of a new X10 device and starts monitoring it.
            '''
            newdevice = Device(parameters["hcdc"])
            
            for value in parameters["values"]:
                
                if value == "alllightsoff":
                    newdevice.alllightsoff = True
                    
                elif value == "alllightson":
                    newdevice.alllightson = True

                elif value == "allunitsoff":
                    newdevice.allunitsoff = True
                    
                elif value == "presetdim":
                    newdevice.presetdim = True
                    
                elif value == "standarddim":
                    newdevice.standarddim = True
                    
                elif value == "statusrequest":
                    newdevice.statusrequest = True

                elif value == "reportstatus":
                    newdevice.reportstatus = True
                
                else:
                    log.msg("Unknown characteristic (%s)" % value)

            listDevices.append(newdevice)
            log.msg("Monitoring new device (%s)" % newdevice.hcdc)

            self.writeXML()

        elif command == 'set_characteristics':
            '''
            This command sets the characteristics of the specified X10 device.
            '''
            item = next((i for i in xrange(len(listDevices)) if listDevices[i].hcdc == parameters["hcdc"]), None)
            if item is not None:
                listDevices[item].alllightsoff = False
                listDevices[item].alllightson = False
                listDevices[item].allunitsoff = False
                listDevices[item].presetdim = False
                listDevices[item].standarddim = False
                listDevices[item].statusrequest = False
                listDevices[item].reportstatus = False

                for value in parameters["values"]:
                    if value == "alllightsoff":
                        listDevices[item].alllightsoff = True
                    
                    if value == "alllightson":
                        listDevices[item].alllightson = True

                    if value == "allunitsoff":
                        listDevices[item].allunitsoff = True
                        
                    if value == "presetdim":
                        listDevices[item].presetdim = True
                        
                    if value == "standarddim":
                        listDevices[item].standarddim = True
                    
                    if value == "statusrequest":
                        listDevices[item].statusrequest = True
                        
                    if value == "reportstatus":
                        listDevices[item].reportstatus = True
                        
            else:
                log.msg("Unknown device specified (%s)" % parameters["hcdc"])

            log.msg("Characteristics of device (%s) are changed" % parameters["hcdc"])

            self.writeXML()

        elif command == 'get_characteristics':
            '''
            This command retrieves the characteristics of the specified X10 device.
            '''
            item = next((i for i in xrange(len(listDevices)) if listDevices[i].hcdc == parameters["hcdc"]), None)
            if item is None:
                log.msg("Error: Retrieved device characteristics of a device not being monitored.")
            else:
                return({"alllightsoff":  listDevices[item].alllightsoff,
                        "allunitsoff":   listDevices[item].allunitsoff,
                        "alllightson":   listDevices[item].alllightson,
                        "standarddim":   listDevices[item].standarddim,
                        "presetdim":     listDevices[item].presetdim,
                        "statusrequest": listDevices[item].statusrequest,
                        "reportstatus":  listDevices[item].reportstatus})

        elif command == 'del_characteristics':
            '''
            This command stops monitoring of the specified X10 device.
            '''
            item = next((i for i in xrange(len(listDevices)) if listDevices[i].hcdc == parameters["hcdc"]), None)
            if item is not None:
                log.msg("Stopped monitoring X10 device (%s)" % parameters["hcdc"])
                
                del listDevices[item]
                
                # Write XML file containing details of monitored devices
                self.writeXML()
                
            else:
                log.msg("Unknown device specified (%s)" % parameters["hcdc"])
                return(None)
            
        else:
            log.msg("unknown command (%s)" % command)

        return {'processed': True}

    def writeXML(self):             
        # Write device characteristics to a file
        xml_file = os.path.join(self.config_path, 'x10.xml')
        file = open(xml_file, 'w')
        root = ET.Element("devices")
            
        for device in listDevices:
            dev = ET.SubElement(root, "device", {"hcdc": device.hcdc})
            char = ET.SubElement(dev, "standarddim")
            char.text = str(device.standarddim)
            char = ET.SubElement(dev, "presetdim")
            char.text = str(device.presetdim)
            char = ET.SubElement(dev, "allunitsoff")
            char.text = str(device.allunitsoff)
            char = ET.SubElement(dev, "alllightsoff")
            char.text = str(device.alllightsoff)
            char = ET.SubElement(dev, "alllightson")
            char.text = str(device.alllightson)
            char = ET.SubElement(dev, "statusrequest")
            char.text = str(device.statusrequest)
            char = ET.SubElement(dev, "reportstatus")
            char.text = str(device.reportstatus)

        # Function to indent XML output
        # Source: http://infix.se/2007/02/06/gentlemen-indent-your-xml
        def indent(elem, level=0):
            i = "\n" + level*"  "
            if len(elem):
                if not elem.text or not elem.text.strip():
                    elem.text = i + "  "
                    
                for e in elem:
                    indent(e, level+1)
                    if not e.tail or not e.tail.strip():
                        e.tail = i + "  "
                        
                if not e.tail or not e.tail.strip():
                        e.tail = i
                        
            else:
                if level and (not elem.tail or not elem.tail.strip()):
                    elem.tail = i
                            
        tree = ET.ElementTree(root)
        indent(tree.getroot())
        tree.write(xml_file)
        file.close()

            
if __name__ == '__main__':
    cm11 = CM11Wrapper()
    cm11.start()