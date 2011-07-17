from mako.lookup import TemplateLookup
from mako.template import Template
from twisted.web.resource import Resource
from twisted.web.server import NOT_DONE_YET
from twisted.internet import defer
import json

def init_pages(web, coordinator, db):
    web.putChild("x10_add", x10_add(coordinator, db))
    web.putChild("x10_edit", x10_edit(coordinator, db))
    web.putChild("x10_man", x10_man(coordinator, db))
    web.putChild("x10_added", x10_added(coordinator, db))
    web.putChild("x10_saved", x10_saved(coordinator, db))
    web.putChild("x10_del", x10_del(coordinator, db))

class x10_add(Resource):
    """
    Class that shows an add form to add an X10 device to the HouseAgent database.
    """
    def __init__(self, coordinator, db):
        Resource.__init__(self)
        self.coordinator = coordinator
        self.db = db
 
    def result(self, result):
 
        lookup = TemplateLookup(directories=['templates/'])
        template = Template(filename='templates/plugins/x10/device_add.html', lookup=lookup)
 
        self.request.write(str(template.render(pluginid=self.pluginid, pluginguid=self.pluginguid, locations=result[0])))
        self.request.finish()
 
    def render_GET(self, request):
 
        self.request = request
        plugins = self.coordinator.get_plugins_by_type("x10")
        
        if len(plugins) == 0:
            self.request.write(str("No online X10 plugins found..."))
            self.request.finish()
        elif len(plugins) == 1:
            self.pluginguid = plugins[0].guid
            self.pluginid = plugins[0].id
        
        deferlist = []
        deferlist.append(self.db.query_locations())
        d = defer.gatherResults(deferlist)
        d.addCallback(self.result)

        return NOT_DONE_YET 

class x10_edit(Resource):
    """
    Class that shows a form to change the selected X10 device.
    """
    def __init__(self, coordinator, db):
        Resource.__init__(self)
        self.coordinator = coordinator
        self.db = db
 
    def device_result(self, result):
        lookup = TemplateLookup(directories=['templates/'])
        template = Template(filename='templates/plugins/x10/device_edit.html', lookup=lookup)
        print"RESULT1: ", result[1]
        print"RESULT2: ", result[2]
        self.request.write(str(template.render(pluginid=self.pluginid, pluginguid=self.pluginguid, device=result[1], locations=result[0], characteristics=result[2]))) 
        self.request.finish()            
         
    def render_GET(self, request):
        self.request = request
        id = request.args["id"][0]
        hcdc = request.args["hcdc"][0]
        
        plugins = self.coordinator.get_plugins_by_type("x10")
        
        if len(plugins) == 0:
            self.request.write(str("No online X10 plugins found..."))
            self.request.finish()
        elif len(plugins) == 1:
            self.pluginguid = plugins[0].guid
            self.pluginid = plugins[0].id
            
        deferlist = []
        deferlist.append(self.db.query_locations())
        deferlist.append(self.db.query_device(int(id)))
        deferlist.append(self.coordinator.send_custom(self.pluginguid, "get_characteristics", {'hcdc': hcdc}))
        d = defer.gatherResults(deferlist)
        d.addCallback(self.device_result)
        
        
        return NOT_DONE_YET

class x10_man(Resource):
    '''
    Template that handles device management in the database.
    '''
    def __init__(self, coordinator, db):
        Resource.__init__(self)
        self.coordinator = coordinator
        self.db = db
        
    def result(self, result):
        lookup = TemplateLookup(directories=['templates/'])
        template = Template(filename='templates/plugins/x10/device_man.html', lookup=lookup)
        
        self.request.write(str(template.render(result=result)))
        self.request.finish()
    
    def render_GET(self, request):
        self.request = request
        plugins = self.coordinator.get_plugins_by_type("x10")

        if len(plugins) == 0:
            self.request.write(str("No online X10 plugins found..."))
            self.request.finish()
            
        elif len(plugins) == 1:
            self.pluginid = plugins[0].id
        
        self.db.query_plugin_devices(self.pluginid).addCallback(self.result)
        
        return NOT_DONE_YET

class x10_added(Resource):
    """
    Class that adds an X10 device to the HouseAgent database.
    """
    def __init__(self, coordinator, db):
        Resource.__init__(self)
        self.coordinator = coordinator      
        self.db = db
        
    def device_added(self, result):
        #log.msg ("Added new X10 device ('%s')" % self.data['hcdc'])
        print "Added new X10 device ", self.data['hcdc']
        
        # Notify the plugin        
        self.coordinator.send_custom(self.data['pluginguid'], "add_characteristics", {'hcdc': self.data['hcdc'], 'values': self.data['valueids']})

        self.request.write(str("done!")) 
        self.request.finish()             
    
    def render_POST(self, request):
        self.request = request
        self.data = json.loads(request.content.read())

        def handle_error(error):
            #log.msg("New device could not be added to the database (%s)" % error)
            print "New device could not be added to the database - ", error

        # Add device to the database
        d = self.db.add_device(self.data['name'], self.data['hcdc'], self.data['pluginid'], self.data['location'])
        d.addCallback(self.device_added)
        d.addErrback(handle_error)
        
        return NOT_DONE_YET
    
class x10_saved(Resource):
    """
    Class to save a modified X10 device to the HouseAgent database.
    """
    def __init__(self, coordinator, db):
        Resource.__init__(self)
        self.coordinator = coordinator      
        self.db = db
        
    def device_edited(self, result):
        #log.msg ("Added new X10 device ('%s')" % self.data['hcdc'])
        print "Edited X10 device ", self.data['hcdc']
        
        # Notify the plugin        
        self.coordinator.send_custom(self.data['pluginguid'], "set_characteristics", {'hcdc': self.data['hcdc'], 'values': self.data['valueids']})

        self.request.write(str("done!")) 
        self.request.finish()             
    
    def render_POST(self, request):
        self.request = request
        self.data = json.loads(request.content.read())

        def handle_error(error):
            #log.msg("New device could not be added to the database (%s)" % error)
            print "Edited device could not be saved to the database - ", error

        try:
            id = self.data["id"]
        except KeyError:
            id = None
        
        print "DEBUG: ", id
        
        # Add device to the database
        d = self.db.save_device(self.data['name'], self.data['hcdc'], self.data['pluginid'], self.data['location'], id)
        d.addCallback(self.device_edited)
        d.addErrback(handle_error)
        
        return NOT_DONE_YET
    
class x10_del(Resource):
    '''
    Class that handles deletion of devices from the database.
    '''
    def __init__(self, coordinator, db):
        Resource.__init__(self)
        self.coordinator = coordinator      
        self.db = db

    def device_deleted(self, result):
        self.request.write(str("done!"))
        self.request.finish()
    
    def render_POST(self, request):
        self.request = request              
        id = request.args["id"][0]
        hcdc = request.args["hcdc"][0]
        print "DEBUG: HCDC=", hcdc
        
        self.db.del_device(id).addCallback(self.device_deleted)
        return NOT_DONE_YET
